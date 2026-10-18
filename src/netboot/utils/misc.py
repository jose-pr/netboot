import re
import shlex
from importlib import import_module
from typing import Any, List, TypeVar, Union

from pathapi import LocalPath, Path
from uripath import URIPath

__all__ = ["T", "import_", "parse_path", "shell_quote"]

T = TypeVar("T")


def import_(name: str) -> Any:
    """Import `"pkg.mod.attr"` and return `attr` (splits on the last dot)."""
    module, obj = name.rsplit(".", maxsplit=1)
    return getattr(import_module(module), obj)


#: RFC 3986 `scheme ":"`, at least two characters so a drive letter is not one.
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")


def parse_path(path: "str | Path") -> Path:
    """Parse a configured path: a bare path is local, ``scheme:`` is a URI.

    A single-letter scheme is a Windows drive, not a URI: ``C:\\srv\\tftp`` and
    ``C:/srv/tftp`` are local paths. Real URI schemes are at least two
    characters, so this costs nothing and stops every absolute Windows path
    from being parsed as a URI (and then failing with `NotImplementedError`
    when something tries to read it).
    """
    if isinstance(path, Path):
        return path
    text = str(path)
    return URIPath(text) if _URI_SCHEME.match(text) else LocalPath(text)


def shell_quote(value: Any) -> "Union[str, List[str]]":
    """`shlex.quote` for a template: a POSIX shell reads the result as literal text.

    A list or tuple is quoted element-wise and comes back as a list; anything
    else gives a `str`. A value that is not a string is stringified first, and
    ``None`` is the empty string (``''``). A value with nothing special to the
    shell comes back as it is.
    """
    if isinstance(value, (list, tuple)):
        return [shell_quote(item) for item in value]
    return shlex.quote("" if value is None else str(value))
