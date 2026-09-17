from argparse import Namespace
from typing import Mapping, TypeVar

#: Explicit, so `from .dicts import *` (in `netboot.utils`) does not re-export
#: `argparse.Namespace` under a name consumers would read as netboot's own
#: config `Namespace` (which lives in `netboot.utils.config`).
__all__ = ["T", "flatten", "arr_get", "shell_quote"]

T = TypeVar("T")


def flatten(map: "dict|Namespace|list", _prefix: str = "") -> dict:
    """Flatten a nested mapping/list into a single-level ``dict``.

    Nested keys are joined with ``_`` (e.g. ``{"a": {"b": 1}}`` ->
    ``{"a_b": 1}``); list items use their index (``{"a": [x, y]}`` ->
    ``{"a_0": x, "a_1": y}``). Scalars are left as-is. Used to turn a render
    context into the flat ``$UPPER`` variables a shell template substitutes.
    """
    if isinstance(map, Namespace):
        map = map.__dict__

    if isinstance(map, Mapping):
        items = ((str(key), val) for key, val in map.items())
    elif isinstance(map, list):
        items = ((str(index), val) for index, val in enumerate(map))
    else:
        return {_prefix: map} if _prefix else map

    result: dict = {}
    for key, val in items:
        full = f"{_prefix}_{key}" if _prefix else key
        if isinstance(val, (Mapping, list, Namespace)):
            result.update(flatten(val, full))
        else:
            result[full] = val
    return result


def arr_get(arr: list, pos: int, default=None):
    if len(arr) > pos:
        return arr[pos]
    else:
        return default


def shell_quote(text: "str|list[str]", quote="'"):
    """Quote value(s) so a POSIX shell reads them as literal text.

    A `str` in gives a `str` out (so ``{{ shell_quote(v) }}`` renders the value,
    not a Python list); a list in gives a list out, quoted element-wise. Values
    that are not strings are stringified first, and ``None`` becomes an empty
    quoted string.

    ``quote="'"`` (the default) is the safe form: nothing inside single quotes
    is special to the shell, and an embedded ``'`` is closed, escaped and
    reopened. ``quote='"'`` keeps the shell's own expansion rules, so only the
    characters that would end the string or start an expansion (``"``, ``\\``,
    ``` ` ```, ``$``) are escaped -- use it only when the template *wants*
    expansion inside the value.
    """
    if quote not in ("'", '"'):
        raise ValueError(f"shell_quote: quote must be ' or \", not {quote!r}")

    def _one(value) -> str:
        value = "" if value is None else str(value)
        if quote == "'":
            return "'" + value.replace("'", "'\\''") + "'"
        escaped = "".join("\\" + char if char in '"\\`$' else char for char in value)
        return '"' + escaped + '"'

    if isinstance(text, (list, tuple)):
        return [_one(item) for item in text]
    return _one(text)
