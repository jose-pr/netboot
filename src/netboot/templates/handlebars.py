from pathlib_next import Path

from ..logging import LOGGER
from .common import DEFAULT_PRIORITY, Template, can_process_suffix
from .data import template_data


def _pybars():
    """Import pybars3, or explain which extra installs it."""
    try:
        from pybars import Compiler
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "the handlebars template engine requires netboot's 'handlebars' "
            "extra: pip install netboot[handlebars]"
        ) from exc
    return Compiler


class HandlebarsTemplate(Template):
    """A Handlebars template (`.hbs` / `.handlebars`), rendered by pybars3.

    Needs the **`handlebars` extra** (`pip install netboot[handlebars]`).

    Like mustache, handlebars has **no strict mode and no warning hook**: an
    undefined name renders as an empty string whatever `templates_undefined`
    says, and the engine logs that once per render under `strict` so the gap is
    not silent. Use Jinja, mako, liquid or the shell engine for an artifact that
    must fail rather than ship a blank.
    """

    EXT = (".hbs", ".handlebars")
    PRIORITY = DEFAULT_PRIORITY

    def __init__(self, template: str) -> None:
        self._source = template
        # Fail at load time, while the file needing the extra is the subject.
        self._compiler = _pybars()()

    def render(self, **extras):
        """Render the template; a missing name is always an empty string."""
        if getattr(self, "_undefined_", "strict") == "strict":
            LOGGER.debug(
                "handlebars cannot fail on an undefined name; "
                "templates_undefined=strict does not apply to this engine"
            )
        data = template_data(getattr(self, "_globals_", None), extras)
        # pybars returns a strlist (a list of fragments), not a string.
        return "".join(self._compiler.compile(self._source)(data))

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for the handlebars suffixes (`cls.EXT`)."""
        return can_process_suffix(cls, file)
