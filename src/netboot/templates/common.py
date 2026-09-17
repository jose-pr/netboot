from typing import TYPE_CHECKING

from jinja2 import Environment as Renderer
from pathlib_next import Path, UriPath

if TYPE_CHECKING:
    from . import Loader


class Template:
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
        pass

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        return False
