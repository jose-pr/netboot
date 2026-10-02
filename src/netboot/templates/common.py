from typing import TYPE_CHECKING, ClassVar, Iterable, Optional, Tuple, Union

from jinja2 import DebugUndefined, StrictUndefined, Undefined
from jinja2 import Environment as Renderer

#: How a template variable that has no value is rendered. The same word means
#: the same thing in both engines, which is the point of the setting: the shell
#: engine always raised while Jinja silently produced an empty string, so the
#: behaviour depended on a file's suffix.
UNDEFINED_MODES = ("strict", "lenient", "debug")

#: `templates_undefined` -> the jinja2 class implementing it.
JINJA_UNDEFINED = {
    "strict": StrictUndefined,  # raise, naming the variable
    "lenient": Undefined,  # render as an empty string
    "debug": DebugUndefined,  # leave `{{ name }}` in the output
}
from pathlib_next import Path, UriPath

if TYPE_CHECKING:
    from . import Loader


class TemplateEngineError(Exception):
    """No configured engine claims a template file.

    Not a `PixieError`: `netboot.engine` imports this package, so the error a
    loader raises cannot live there.
    """


#: An `EXT` entry meaning "any suffix", for an engine that is deliberately a
#: catch-all. `EXT = None` means the same thing.
ANY_SUFFIX = "*"

#: `PRIORITY` for an ordinary engine that claims its own suffixes.
DEFAULT_PRIORITY = 0
#: `PRIORITY` for a last-resort catch-all, which every other engine must get a
#: look in before: `CopyTemplate` uses it.
FALLBACK_PRIORITY = -100

#: Engines a `Loader` considers when it is not given an explicit list, in
#: registration order; `Loader` sorts them by `PRIORITY`. Mutate through
#: `register_template_type` / `unregister_template_type` rather than directly.
TEMPLATE_TYPES: list = []


def register_template_type(cls=None, *, priority: Optional[int] = None):
    """Add an engine to the default `template_types`, and return it.

    Usable bare or as a decorator, with an optional `priority` override for an
    engine whose class you do not own:

    ```python
    @register_template_type                 # or: (priority=10)
    class MakoTemplate(Template):           # your engine; netboot ships none
        EXT = ".mako"                       # for mako
    ```

    Registration order does **not** decide what wins -- `PRIORITY` does -- so a
    plugin loaded after the shipped engines is still consulted before the
    catch-all `CopyTemplate`, which is the whole point of the two living apart.
    Registering the same class twice is a no-op, so importing a plugin module
    twice does not double it.
    """

    def _register(cls):
        if priority is not None:
            cls.PRIORITY = priority
        if cls not in TEMPLATE_TYPES:
            TEMPLATE_TYPES.append(cls)
        return cls

    return _register if cls is None else _register(cls)


def unregister_template_type(cls) -> bool:
    """Drop an engine from the default `template_types`; True if it was there."""
    if cls in TEMPLATE_TYPES:
        TEMPLATE_TYPES.remove(cls)
        return True
    return False


def by_priority(template_types: Iterable) -> Tuple:
    """`template_types` ordered highest `PRIORITY` first.

    A stable sort, so engines of equal priority keep the order they were given
    -- an explicit `template_types` list still means what it says, and only a
    different priority moves anything.
    """
    return tuple(
        sorted(
            template_types,
            key=lambda t: -int(getattr(t, "PRIORITY", DEFAULT_PRIORITY)),
        )
    )


def template_extensions(cls) -> Optional[Tuple[str, ...]]:
    """The suffixes `cls` claims, or `None` for "any suffix".

    `EXT` is written for the person subclassing, not for the comparison: a
    single string (`EXT = "shtpl"`) and a missing leading dot both work, and
    matching is case-insensitive.
    """
    ext: Union[None, str, Iterable[str]] = getattr(cls, "EXT", ())
    if ext is None:
        return None
    if isinstance(ext, str):
        ext = (ext,)
    suffixes = []
    for entry in ext:
        entry = str(entry)
        if entry in (ANY_SUFFIX, "." + ANY_SUFFIX):
            return None
        if not entry.startswith("."):
            entry = "." + entry
        suffixes.append(entry.lower())
    return tuple(suffixes)


def can_process_suffix(cls, file) -> bool:
    """Whether `file`'s suffix is one of `cls.EXT`."""
    suffixes = template_extensions(cls)
    if suffixes is None:
        return True
    return str(getattr(file, "suffix", "")).lower() in suffixes


class Template:
    """The minimal template contract: render, and say what you can process."""

    loader: "Loader"

    #: Suffixes this engine renders. `()` claims nothing, `None` (or `"*"`)
    #: claims everything -- see `template_extensions`.
    EXT: ClassVar[Union[None, str, Tuple[str, ...]]] = ()
    #: Engines are consulted **highest first**, so an engine that claims a
    #: suffix outranks a catch-all without anyone having to order a list:
    #: `DEFAULT_PRIORITY` (0) for a normal engine, `FALLBACK_PRIORITY` (-100)
    #: for a last resort. Equal priorities keep the order they were given in.
    PRIORITY: ClassVar[int] = DEFAULT_PRIORITY
    #: True to be constructed from the file's raw `bytes` rather than decoded
    #: text, and to return `bytes` from `render()`. The loader only decodes for
    #: engines that say they need text, so a file that is not UTF-8 at all can
    #: still reach a binary engine.
    BINARY: ClassVar[bool] = False

    def __init__(self, template: str) -> None:
        pass

    @property
    def is_up_to_date(self) -> bool:
        """Whether the source file is unchanged since this was loaded.

        The loader hands us a *callable*; assigning it straight to
        `is_up_to_date` made every cached template look fresh forever, since a
        function object is always truthy.
        """
        check = getattr(self, "_uptodate_", None)
        return check() if callable(check) else True

    def render(self, **globals):
        """Render this template and return the text."""
        pass

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """Can this engine render `file`? Checked in `template_types` order."""
        return can_process_suffix(cls, file)
