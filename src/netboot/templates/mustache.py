from pathlib_next import Path

from ..logging import LOGGER
from .common import DEFAULT_PRIORITY, Template, can_process_suffix
from .data import template_data


def _chevron():
    """Import chevron, or explain which extra installs it."""
    try:
        import chevron
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "the mustache template engine requires netboot's 'mustache' extra: "
            "pip install netboot[mustache]"
        ) from exc
    return chevron


class MustacheTemplate(Template):
    """A Mustache template (`.mustache`), rendered by chevron.

    Needs the **`mustache` extra** (`pip install netboot[mustache]`).

    Mustache is deliberately logic-less, and chevron has **no strict mode**: a
    name with no value renders as an empty string, whatever `templates_undefined`
    says. Under `strict` the engine turns on chevron's own `warn`, so a missing
    name at least reaches the log -- `strict` cannot be made to fail here, and a
    boot artifact that must not ship with a blank belongs in an engine that can
    (Jinja, mako, liquid or the shell engine).
    """

    EXT = (".mustache",)
    PRIORITY = DEFAULT_PRIORITY

    def __init__(self, template: str) -> None:
        self._source = template
        # Fail at load time, while the file needing the extra is the subject.
        self._chevron = _chevron()

    def render(self, **extras):
        """Render the template; a missing name is always an empty string."""
        mode = getattr(self, "_undefined_", "strict")
        data = template_data(getattr(self, "_globals_", None), extras)
        if mode == "strict":
            LOGGER.debug(
                "mustache cannot fail on an undefined name; rendering with "
                "warnings instead of templates_undefined=strict"
            )
        return self._chevron.render(self._source, data, warn=mode == "strict")

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for the mustache suffixes (`cls.EXT`)."""
        return can_process_suffix(cls, file)
