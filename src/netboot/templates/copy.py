from pathlib_next import Path

from ..logging import LOGGER
from .common import Template, can_process_suffix, template_extensions
from .shell import ShellTemplate


def _has_shell_placeholders(source: bytes) -> bool:
    """Does `source` carry a substitutable shell placeholder?

    Decoded leniently on purpose: this is a heads-up about a template that
    should have been renamed, so an undecodable byte somewhere else in the file
    must not get in the way. An escaped delimiter (`%%`) matches the pattern
    too, so only the named and braced groups count -- otherwise a file
    mentioning `100%%` would be reported.
    """
    text = source.decode("utf-8", errors="ignore")
    for match in ShellTemplate._regex().finditer(text):
        groups = match.groupdict()
        if groups.get("braced") or groups.get("named"):
            return True
    return False


class CopyTemplate(Template):
    """The catch-all: hand back the file's **bytes**, unchanged.

    A template whose suffix no other engine claims is not an error, it is a
    file to copy -- a static `grub.cfg`, an EFI binary, a license. It claims
    **any** suffix, so it belongs **last** in `template_types`.

    `BINARY = True`, so the loader never decodes the file: `.render()` returns
    `bytes` and the copy is byte-exact whatever the file holds -- a CRLF
    artifact, latin-1 text, or something that is not text at all. A caller that
    writes the result needs `write_bytes`, not `write_text`.

    Because it renders nothing, a file that *was* a shell template before the
    engine took its own suffix would silently ship its placeholders unchanged.
    That one case is worth a warning rather than a surprise, so a source still
    carrying `%{NAME}` placeholders says so.
    """

    #: `None` claims every suffix -- see `template_extensions`.
    EXT = None
    #: Hand this engine the raw bytes, not decoded text.
    BINARY = True

    def __init__(self, template: bytes) -> None:
        self._source = (
            template if isinstance(template, bytes) else str(template).encode("utf-8")
        )

    def render(self, **extras) -> bytes:
        """Return the source bytes untouched."""
        if _has_shell_placeholders(self._source):
            suffix = (template_extensions(ShellTemplate) or (".shtpl",))[0]
            LOGGER.warning(
                "template copied as is but still contains %s placeholders; "
                "rename it to *%s for them to be substituted",
                ShellTemplate.DELIMITER + "{NAME}",
                suffix,
            )
        return self._source

    @classmethod
    def can_process(cls, file: Path, template) -> bool:
        """True for anything, unless a subclass narrows `EXT`."""
        return can_process_suffix(cls, file)
