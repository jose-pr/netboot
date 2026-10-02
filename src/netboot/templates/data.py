"""A mapping view of the render context, for engines that only read data.

jinja2 and mako evaluate Python expressions, so they take the `PixieContext`
itself. mustache, handlebars and liquid are data languages: liquid cannot reach
an attribute at all (`{{ ctx.target.hostname }}` renders empty for an object),
and the other two can, but only by their own rules. One view keeps the namespace
the same in all three.
"""

from collections.abc import Mapping, Sequence
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

#: Values handed through as they are: a template writes them with `{{ }}`, which
#: stringifies them exactly as the other engines do. Anything else with a
#: `__dict__` becomes a view.
_SCALARS = (
    str,
    bytes,
    bool,
    int,
    float,
    complex,
    type(None),
    IPv4Address,
    IPv6Address,
    IPv4Network,
    IPv6Network,
)


def wrap(value):
    """Wrap `value` for a data template: views for structures, scalars as they are."""
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, Mapping):
        return DataView(value)
    if isinstance(value, (list, tuple)) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    ):
        return [wrap(item) for item in value]
    if hasattr(value, "__dict__") or hasattr(value, "__slots__"):
        return DataView(value)
    return value


class DataView(Mapping):
    """Read `obj`'s items (a mapping) or attributes (anything else), lazily.

    Lazy on purpose: a context holds targets, images, zones and repos, and a
    template usually touches a handful of them. Nothing is copied, so a view
    never goes stale against the context it wraps.
    """

    __slots__ = ("_obj",)

    def __init__(self, obj) -> None:
        object.__setattr__(self, "_obj", obj)

    def _raw(self, key):
        obj = object.__getattribute__(self, "_obj")
        if isinstance(obj, Mapping):
            return obj[key]
        try:
            return getattr(obj, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __getitem__(self, key):
        return wrap(self._raw(key))

    def __iter__(self):
        obj = object.__getattribute__(self, "_obj")
        if isinstance(obj, Mapping):
            return iter(obj)
        names = list(getattr(obj, "__dict__", {}) or {})
        for slot in getattr(type(obj), "__slots__", ()) or ():
            if slot not in names:
                names.append(slot)
        # Dunders are machinery, never template data. A single underscore stays:
        # `image._id` is the image's name, and real templates use it.
        return iter(name for name in names if not name.startswith("__"))

    def __len__(self) -> int:
        return sum(1 for _ in iter(self))

    def __getattr__(self, name):
        """Attribute access too, so a helper written for `ctx` still works."""
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DataView({object.__getattribute__(self, '_obj')!r})"


def jsonable(
    value, _seen: "frozenset|None" = None, _depth: int = 0, max_depth: int = 8
):
    """A JSON-serialisable copy of a data view, for an engine in another language.

    Anything that is not a structure or a JSON scalar is stringified, the way a
    template would have written it. Two guards matter here and not in `DataView`,
    which is lazy: the context reaches back to the engine (`ctx._netboot_`) and
    holds its own renderer, so a naive walk would serialise the whole program and
    never terminate. Keys starting with `_` are dropped for that reason --
    except `_id`, which is an image's or target's name and is used in templates.
    """
    _seen = _seen or frozenset()
    if isinstance(value, (str, bool, int, float, type(None))):
        return value
    if _depth >= max_depth:
        return str(value)
    if isinstance(value, (DataView, Mapping)):
        if id(value) in _seen:  # pragma: no cover - defensive
            return None
        seen = _seen | {id(value)}
        out = {}
        for key in value:
            key = str(key)
            if key.startswith("_") and key != "_id":
                continue
            try:
                item = value[key]
            except Exception:  # pragma: no cover - a property that raises
                continue
            out[key] = jsonable(item, seen, _depth + 1, max_depth)
        return out
    if isinstance(value, (list, tuple)):
        seen = _seen | {id(value)}
        return [jsonable(item, seen, _depth + 1, max_depth) for item in value]
    return str(value)


def template_data(globals_: "Mapping|None", extras: "Mapping|None" = None) -> dict:
    """The names a data template can use.

    `ctx` wrapped in a `DataView`, plus the context's own attributes at the top
    level -- `{{ target.hostname }}` and `{{ ctx.target.hostname }}` both work --
    then everything else in the render globals, and finally `extras`. Later wins,
    so a global never shadows an explicit extra.
    """
    data: dict = {}
    globals_ = globals_ or {}
    ctx = globals_.get("ctx")
    if ctx is not None:
        view = wrap(ctx)
        data["ctx"] = view
        if isinstance(view, DataView):
            for key in view:
                data[key] = view[key]
    for key, value in globals_.items():
        if key == "ctx":
            continue
        data[key] = wrap(value)
    for key, value in (extras or {}).items():
        data[key] = wrap(value)
    return data
