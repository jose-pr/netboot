from jinja2 import Template as _Jinja2Template
from pathlib_next import Path, UriPath

from ..utils import shell_quote
from .common import Renderer, Template  # noqa: F401 - re-exported for callers


class JinjaTemplate(_Jinja2Template):
    """A Jinja2 template (`.j2`/`.jinja`/`.jinja2`)."""

    def render(self, **globals):
        """Render with netboot's extra globals (`shell_quote`, `Path`, `Uri`).

        `Path` is pathlib_next's, not the stdlib's: a template may name a URI
        path, and the two are not interchangeable.
        """
        return super().render(
            shell_quote=shell_quote, Path=Path, Uri=UriPath, **globals
        )

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for the Jinja suffixes."""
        return file.suffix in [".j2", ".jinja", ".jinja2"]
