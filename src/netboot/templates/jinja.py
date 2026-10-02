from jinja2 import Template as _Jinja2Template
from pathlib_next import Path, UriPath

from ..utils import shell_quote
from .common import (  # noqa: F401 - Renderer/Template re-exported for callers
    DEFAULT_PRIORITY,
    Renderer,
    Template,
    can_process_suffix,
)


class JinjaTemplate(_Jinja2Template):
    """A Jinja2 template (`.j2`/`.jinja`/`.jinja2`).

    `EXT` is the suffix list, so a subclass can claim others (`.html.j2` is
    already covered -- a suffix is the last one only).
    """

    #: Not inherited from `Template`: a jinja2 template cannot share our base,
    #: so the contract's attributes are spelled out here.
    EXT = (".j2", ".jinja", ".jinja2")
    PRIORITY = DEFAULT_PRIORITY
    BINARY = False

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
        """True for the Jinja suffixes (`cls.EXT`)."""
        return can_process_suffix(cls, file)
