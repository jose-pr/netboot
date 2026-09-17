from importlib import import_module

from pathlib_next import LocalPath, Path, UriPath

__all__ = ["import_", "parse_path"]


def import_(name: str):
    """Import `"pkg.mod.attr"` and return `attr` (splits on the last dot)."""
    module, obj = name.rsplit(".", maxsplit=1)
    return getattr(import_module(module), obj)


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
    scheme, sep, _ = text.partition(":")
    if not sep or len(scheme) < 2 or not scheme.isalnum():
        return LocalPath(text)
    return UriPath(text)
