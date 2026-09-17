from typing import TYPE_CHECKING

from jinja2 import DebugUndefined, StrictUndefined, Undefined
from jinja2 import Environment as Renderer

#: How a template variable that has no value is rendered. The same word means
#: the same thing in both engines, which is the point of the setting: the shell
#: engine always raised while Jinja silently produced an empty string, so the
#: behaviour depended on a file's suffix.
UNDEFINED_MODES = ("strict", "lenient", "debug")

#: `templates_undefined` -> the jinja2 class implementing it.
JINJA_UNDEFINED = {
    "strict": StrictUndefined,  # raise, naming the variable
    "lenient": Undefined,  # render as an empty string
    "debug": DebugUndefined,  # leave `{{ name }}` in the output
}
from pathlib_next import Path, UriPath

if TYPE_CHECKING:
    from . import Loader


class Template:
    """The minimal template contract: render, and say what you can process."""

    loader: "Loader"

    def __init__(self, template: str) -> None:
        pass

    @property
    def is_up_to_date(self) -> bool:
        """Whether the source file is unchanged since this was loaded.

        The loader hands us a *callable*; assigning it straight to
        `is_up_to_date` made every cached template look fresh forever, since a
        function object is always truthy.
        """
        check = getattr(self, "_uptodate_", None)
        return check() if callable(check) else True

    def render(self, **globals):
        """Render this template and return the text."""
        pass

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """Can this engine render `file`? Checked in `template_types` order."""
        return False
