from pathlib_next import Path

from .common import DEFAULT_PRIORITY, Template, can_process_suffix
from .data import template_data


def _liquid():
    """Import python-liquid, or explain which extra installs it."""
    try:
        import liquid
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "the liquid template engine requires netboot's 'liquid' extra: "
            "pip install netboot[liquid]"
        ) from exc
    return liquid


class LiquidTemplate(Template):
    """A Liquid template (`.liquid`), if python-liquid is installed.

    Needs the **`liquid` extra** (`pip install netboot[liquid]`).

    Liquid is a data language: it reads mappings, not attributes, so the context
    is passed through `template_data` -- `{{ ctx.target.hostname }}` and
    `{{ target.hostname }}` both resolve, and nothing is copied to make that
    work. It is the only optional engine that honours all three
    `templates_undefined` modes, because python-liquid ships an undefined class
    for each: raise, render empty, or leave `{{ name }}` in the output.
    """

    EXT = (".liquid",)
    PRIORITY = DEFAULT_PRIORITY

    def __init__(self, template: str) -> None:
        self._source = template
        # Fail at load time, while the file needing the extra is the subject.
        self._liquid = _liquid()

    def _environment(self, mode: str):
        liquid = self._liquid
        undefined = {
            "strict": liquid.StrictUndefined,
            "lenient": liquid.Undefined,
            "debug": liquid.DebugUndefined,
        }.get(mode, liquid.StrictUndefined)
        return liquid.Environment(undefined=undefined)

    def render(self, **extras):
        """Render the template, mapping `templates_undefined` onto liquid's."""
        mode = getattr(self, "_undefined_", "strict")
        data = template_data(getattr(self, "_globals_", None), extras)
        environment = self._environment(mode)
        return environment.from_string(self._source).render(**data)

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for the liquid suffixes (`cls.EXT`)."""
        return can_process_suffix(cls, file)
