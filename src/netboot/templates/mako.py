from io import StringIO

from pathlib_next import Path, UriPath

from ..logging import LOGGER
from ..utils import shell_quote
from .common import DEFAULT_PRIORITY, Template, can_process_suffix

#: Names mako owns inside a template; a config global called one of these would
#: collide with the render machinery, so it is dropped with a warning instead.
RESERVED_NAMES = frozenset(
    {
        "self",
        "context",
        "capture",
        "caller",
        "loop",
        "next",
        "parent",
        "pageargs",
        "UNDEFINED",
    }
)

_BLANKS_CONTEXT = None


def _mako():
    """Import mako, or explain which extra installs it.

    Imported here rather than at module import so `netboot.templates` -- and
    therefore every netboot install -- stays free of the dependency. The engine
    is registered either way: a `.mako` file must fail saying what is missing,
    not get copied as bytes because nothing claimed it.
    """
    try:
        from mako import runtime
        from mako.template import Template as _MakoTemplate
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "the mako template engine requires netboot's 'mako' extra: "
            "pip install netboot[mako]"
        ) from exc
    return _MakoTemplate, runtime


def _blanks_context(runtime):
    """A mako `Context` whose undefined names render as empty strings.

    mako has no lenient mode of its own: an undefined name reaches the template
    as `UNDEFINED`, which raises `NameError` the moment it is written out. The
    lookup the compiled template performs is `context.get(name, UNDEFINED)`, so
    intercepting `get` is what implements `templates_undefined: lenient` here.
    """
    global _BLANKS_CONTEXT
    if _BLANKS_CONTEXT is None:

        class _Blanks(runtime.Context):
            def get(self, key, default=None):
                value = super().get(key, default)
                if value is runtime.UNDEFINED:
                    LOGGER.warning(
                        "mako template: %s has no value; rendering empty", key
                    )
                    return ""
                return value

        _BLANKS_CONTEXT = _Blanks
    return _BLANKS_CONTEXT


class MakoTemplate(Template):
    """A mako template (`.mako`), if mako is installed.

    Needs the **`mako` extra** (`pip install netboot[mako]`): nothing else in
    netboot imports mako, and the engine is registered whether or not it is
    there, so a `.mako` file raises `ImportError` naming the extra rather than
    being silently copied by the fallback engine.

    The render namespace matches the Jinja engine's: `ctx` plus `shell_quote`,
    `Path` (pathlib_next's) and `Uri`, so `${ctx.target.hostname}` and
    `${shell_quote(password)}` mean the same thing in both.

    `templates_undefined` maps as far as mako allows: `strict` becomes mako's
    own `strict_undefined` (a `NameError` naming the variable), while `lenient`
    renders an undefined name as an empty string and warns. **`debug` behaves as
    `lenient`** -- a compiled mako template cannot re-emit the `${...}` it came
    from, so there is no placeholder left to leave in place.
    """

    EXT = (".mako",)
    PRIORITY = DEFAULT_PRIORITY

    def __init__(self, template: str) -> None:
        self._source = template
        # Fail at load time, while the file that needs the extra is still the
        # obvious subject, rather than at render.
        self._mako, self._runtime = _mako()

    def _namespace(self, extras: dict) -> dict:
        """The names a template can use: the render globals plus our helpers."""
        data = {"shell_quote": shell_quote, "Path": Path, "Uri": UriPath}
        for source in (getattr(self, "_globals_", None) or {}, extras):
            for key, value in source.items():
                if key in RESERVED_NAMES:
                    LOGGER.warning(
                        "mako template: %r is reserved by mako and is not "
                        "available to the template",
                        key,
                    )
                    continue
                data[key] = value
        return data

    def render(self, **extras):
        """Render the template, honouring `templates_undefined` where mako can."""
        mode = getattr(self, "_undefined_", "strict")
        data = self._namespace(extras)
        # Compiled per render: `_undefined_` is set by the loader after this
        # object is built, and it decides how mako is configured.
        compiled = self._mako(self._source, strict_undefined=mode == "strict")
        if mode == "strict":
            return compiled.render(**data)
        buffer = StringIO()
        compiled.render_context(_blanks_context(self._runtime)(buffer, **data))
        return buffer.getvalue()

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for the mako suffixes (`cls.EXT`)."""
        return can_process_suffix(cls, file)
