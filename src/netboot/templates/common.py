from typing import TYPE_CHECKING

from jinja2 import Environment as Renderer
from pathlib_next import Path, UriPath

if TYPE_CHECKING:
    from . import Loader


class Template:
    loader: "Loader"

    def __init__(self, template: str) -> None:
        pass

    def render(self, **globals):
        pass

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        return False
