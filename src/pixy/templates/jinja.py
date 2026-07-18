from pathlib import Path

from jinja2 import Template as _Jinja2Template

from ..utils import shell_quote
from .common import *


class JinjaTemplate(_Jinja2Template):

    def render(self, **globals):
        return super().render(
            shell_quote=shell_quote, Path=Path, Uri=UriPath, **globals
        )

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        return file.suffix in [".j2", ".jinja", ".jinja2"]
