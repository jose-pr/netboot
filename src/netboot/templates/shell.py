import re
from typing import ClassVar, Tuple, Union

from pathlib_next import Path

from ..logging import LOGGER
from ..utils import flatten
from .common import Template, can_process_suffix

#: A placeholder name: the identifier rules `string.Template` used, matched
#: case-insensitively because the context is flattened to UPPER keys.
_IDENT = r"[_a-z][_a-z0-9]*"

#: Accepted `PATTERN` values: which spellings of a placeholder substitute.
PATTERNS = ("braced", "unbraced", "all")

#: The POSIX "use this when there is no value" operators, longest first.
#: `:-` counts an empty value as no value, `-` only an unset one. The rest of
#: the POSIX set (`:=`, `:?`, `:+`) is deliberately not implemented.
DEFAULT_OPERATORS = (":-", "-")


class ShellTemplate(Template):
    """`%{UPPER_SNAKE}` placeholders substituted from the flattened context.

    Configured by subclassing -- the three class attributes are the whole
    surface:

    ```python
    class DollarTemplate(ShellTemplate):
        EXT = (".tpl", ".cfg")   # a single string works too
        DELIMITER = "$"
        PATTERN = "all"         # ${NAME} and $NAME
    ```

    Only the braced form carries a default (`%{NAME:-fallback}`), and only the
    braced form is on by default, so a bare `%` is ordinary text: a kickstart
    keeps its `%packages`/`%pre`/`%post` sections and a script keeps
    `date +%Y%m%d`.
    """

    #: Suffixes this engine claims. `None` (or `"*"`) restores the pre-0.3
    #: catch-all, which then belongs **last** in `template_types`.
    EXT: ClassVar[Union[None, str, Tuple[str, ...]]] = (".shtpl",)
    #: The character (or string) that introduces a placeholder. Doubling it
    #: escapes it: with the default, `%%` renders as `%`.
    DELIMITER: ClassVar[str] = "%"
    #: One of `PATTERNS`: `braced` for `%{NAME}` only, `unbraced` for `%NAME`
    #: only, `all` for both.
    PATTERN: ClassVar[str] = "braced"

    def __init__(self, template: str) -> None:
        self._source = template

    @classmethod
    def _regex(cls) -> "re.Pattern":
        """The compiled placeholder pattern for this class.

        Cached in `cls.__dict__` rather than as an inherited attribute, so a
        subclass never picks up the pattern its parent compiled from a
        different delimiter.
        """
        cached = cls.__dict__.get("_compiled_pattern")
        if cached is not None:
            return cached

        if cls.PATTERN not in PATTERNS:
            raise ValueError(
                f"{cls.__name__}.PATTERN must be one of "
                f"{', '.join(PATTERNS)}, not {cls.PATTERN!r}"
            )
        delimiter = re.escape(str(cls.DELIMITER))
        operators = "|".join(re.escape(op) for op in DEFAULT_OPERATORS)
        # No `invalid` group: an unmatched delimiter must stay literal text
        # rather than raise, which is what makes a kickstart renderable.
        alternatives = [rf"(?P<escaped>{delimiter})"]
        if cls.PATTERN in ("braced", "all"):
            alternatives.append(
                rf"\{{(?P<braced>{_IDENT})"
                rf"(?:(?P<operator>{operators})(?P<default>[^}}]*))?\}}"
            )
        if cls.PATTERN in ("unbraced", "all"):
            alternatives.append(rf"(?P<named>{_IDENT})")
        compiled = re.compile(
            delimiter + "(?:" + "|".join(alternatives) + ")", re.IGNORECASE
        )
        cls._compiled_pattern = compiled
        return compiled

    def _variables(self) -> dict:
        """The render context, flattened to UPPER string keys."""
        _globals = getattr(self, "_globals_", {})
        context = _globals.get("ctx", {})
        variables = {}
        for key, value in flatten(context).items():
            if value is None:
                value = ""
            elif isinstance(value, bool):
                value = str(value).lower()
            else:
                value = str(value)
            key = key.upper()
            if key in variables and variables[key] != value:
                # `domain` and `DOMAIN` are different context keys but one
                # placeholder; say which value the template will get.
                LOGGER.warning(
                    "shell template variable %s has two sources (%r and %r); "
                    "using the later",
                    key,
                    variables[key],
                    value,
                )
            variables[key] = value
        return variables

    @staticmethod
    def _unquote(default: str) -> str:
        """Strip one layer of matching quotes from a default expression.

        `%{NAME:-'a default'}` is written the POSIX way; the quotes are the
        shell's, not part of the value.
        """
        if len(default) >= 2 and default[0] == default[-1] and default[0] in "\"'":
            return default[1:-1]
        return default

    def render(self, **extras):
        """Substitute the flattened context into the template."""
        variables = self._variables()
        mode = getattr(self, "_undefined_", "strict")

        def substitute(match: "re.Match") -> str:
            groups = match.groupdict()
            if groups.get("escaped") is not None:
                return str(self.DELIMITER)
            name = groups.get("braced") or groups.get("named")
            operator = groups.get("operator")
            value = variables.get(name.upper())
            if operator is not None and (
                value is None or (operator == ":-" and value == "")
            ):
                return self._unquote(groups.get("default") or "")
            if value is not None:
                return value
            if mode == "debug":
                # Leave the placeholder in the output so the gap is visible.
                return match.group(0)
            if mode == "lenient":
                LOGGER.warning(
                    "shell template: %s has no value; rendering empty",
                    match.group(0),
                )
                return ""
            raise KeyError(name)

        return self._regex().sub(substitute, self._source)

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for this engine's suffixes (`.shtpl` unless `EXT` says otherwise)."""
        return can_process_suffix(cls, file)
