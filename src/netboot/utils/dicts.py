from argparse import Namespace
from typing import Mapping, TypeVar

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


def shell_quote(text: "str|list[str]", quote='"'):
    is_arr = isinstance(text, list)
    if not is_arr:
        text = [text]
    result: list[str] = []
    for t in text:
        result.append(f"{quote}{t}{quote}")
    return result
