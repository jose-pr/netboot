"""The netboot engine: targets, images, the render context and the hook chain.

Imported wholesale by the package root, so `from netboot import Pixie` is
unchanged; this module exists so the package root is a surface rather than the
whole implementation.
"""

import enum as _enum
import typing as _ty
from copy import deepcopy
from typing import Mapping, Union, get_type_hints

import netimps
import pathapi
import tplx
from netimps import IPAddress, MACAddress
from tplx import UNDEFINED_MODES
from uripath import URIPath
from anycfg import typed_merge as mergeObjects

from ._version import __version__
from .content import Repository
from .dhcp import DHCPZone, codes_for
from .exceptions import PixieConfigError, PixieError, PixieLookupError
from .logging import LOGGER
from .utils import T, shell_quote
from .utils.misc import import_, parse_path
from ._context import PixieContext
from ._objects import PixieImage, PixieTarget

__all__ = ["Pixie", "PixieContext", "PixieEvent", "PixieImage", "PixieTarget"]

# Compat: StrEnum introduced in Python 3.11; emulate for older Pythons
try:
    from enum import StrEnum
except ImportError:

    class StrEnum(str, _enum.Enum):
        def __str__(self):
            return self.value


class PixieEvent(StrEnum):
    """The points in the engine where hooks run.

    The string value carries the `PixieEvent.` prefix (`PixieEvent.LookupTarget`),
    which hooks comparing against raw strings rely on.
    """

    NewPixieObject = "PixieEvent.NewPixieObject"
    StartPixieInit = "PixieEvent.StartPixieInit"
    SetPixieProperty = "PixieEvent.SetPixieProperty"
    PixieInitiated = "PixieEvent.PixieInitiated"
    LookupTarget = "PixieEvent.LookupTarget"
    FoundTarget = "PixieEvent.FoundTarget"
    FoundTargetImage = "PixieEvent.FoundTargetImage"
    FoundTargetDhcpzone = "PixieEvent.FoundTargetDhcpzone"
    PixieContextForTarget = "PixieEvent.PixieContextForTarget"
    BuildDhcpOptions = "PixieEvent.BuildDhcpOptions"
    StartPixieInitialize = "PixieEvent.StartPixieInitialize"
    EndPixieInitialize = "PixieEvent.EndPixieInitialize"
    StartPixieComplete = "PixieEvent.StartPixieComplete"
    EndPixieComplete = "PixieEvent.EndPixieComplete"


_PixieHook = _ty.Callable[["PixieEvent", "Pixie", T, dict], T]

#: Top-level config keys the engine reads without an annotation.
_TOP_LEVEL_KEYS = frozenset({"dhcp", "defaults", "templates"})


def _config_keys(cls: type) -> "frozenset[str]":
    """The config keys `cls` reads: its annotated fields and `_CONFIG_KEYS`."""
    keys = set(getattr(cls, "_CONFIG_KEYS", ()))
    for klass in cls.__mro__:
        keys.update(getattr(klass, "__annotations__", {}))
    return frozenset(key for key in keys if not key.startswith("_"))


def _warn_unknown_keys(where: str, values: Mapping, known: "frozenset[str]") -> None:
    """Warn naming each key of `values` that nothing reads; `_`-keys are private."""
    for key in values:
        if isinstance(key, str) and not key.startswith("_") and key not in known:
            LOGGER.warning(
                "config key %s%s is not one netboot reads (misspelled? data for "
                "templates belongs under globals)",
                where,
                key,
            )


class Pixie:
    """The engine: config in, provisioning decisions out.

    Built from a merged config mapping, it resolves targets, images and DHCP
    zones, builds render contexts, and arms or disarms DHCP through the
    `initialize`/`complete` lifecycle. Every step fires a `PixieEvent`, so
    behaviour can be changed without subclassing.
    """

    targets: "dict[str,PixieTarget]"
    dhcpzones: "dict[str,DHCPZone]"
    images: "dict[str, PixieImage]"
    repos: "dict[str,Repository]"
    globals: dict[str, object]
    #: What a template variable with no value does: "strict" raises naming it,
    #: "lenient" renders an empty string, "debug" leaves the placeholder in the
    #: output. Set from the config key of the same name; it means the same in
    #: both template engines.
    templates_undefined: str = "strict"
    _ctxcls: "type[PixieContext]" = PixieContext
    VERSION = __version__
    _config: dict
    _hooks: list[_PixieHook] = []

    def hook(
        self: "Pixie|_ty.Sequence[_PixieHook]",
        event: PixieEvent,
        __value: T = None,
        /,
        **kwargs: object,
    ) -> T:
        """Run the hook chain for `event`, threading `value` through it.

        Each hook is called as `f(event, netboot, value, kwargs)` and **must
        return** the value to pass on. `value` is positional-only.
        """
        LOGGER.debug(f"Running Hooks for: {event}")
        if isinstance(self, Pixie):
            netboot = self
            hooks = self._hooks
        else:
            netboot = None
            hooks = self
        for hook in hooks:
            __value = hook(event, netboot, __value, kwargs)
        return __value

    def __new__(
        cls,
        /,
        hooks: "_ty.Optional[_ty.Sequence[_PixieHook]]" = (),
        **config: object,
    ) -> "Pixie":

        hooks = [(hook if callable(hook) else import_(hook)) for hook in hooks or ()]
        cls = Pixie.hook(hooks, PixieEvent.NewPixieObject, cls, config=config)
        if not isinstance(cls, type) or not issubclass(cls, Pixie):
            # The event threads the class through the chain, so a hook that
            # forgets to return it replaces the engine class with None.
            raise PixieConfigError(
                "a NewPixieObject hook must return a Pixie subclass; got " f"{cls!r}"
            )
        inst = object.__new__(cls)
        inst._hooks = hooks
        return inst

    def __init__(
        netboot,
        /,
        **config: object,
    ) -> None:
        # Use what the hook returned, everywhere. Storing it while reading
        # the original meant a hook that returned a new mapping (the natural
        # non-mutating style) had its targets ignored but its templates
        # honoured -- the config ended up half applied.
        config = netboot._config = netboot.hook(PixieEvent.StartPixieInit, config)
        if config is None:
            raise PixieConfigError(
                "a StartPixieInit hook returned None; it must return the config "
                "mapping to pass on"
            )
        _warn_unknown_keys(
            "", config, _config_keys(netboot.__class__) | _TOP_LEVEL_KEYS
        )
        netboot.globals = deepcopy(config.get("globals") or {})
        # Read before the zones, whose servers' URIs may use the names declared.
        netboot.option_codes = codes_for(config.get("dhcp"))
        defaults = config.get("defaults") or {}

        for prop, hint in get_type_hints(netboot.__class__).items():
            if prop.startswith("_"):
                continue
            origin = _ty.get_origin(hint) or hint
            value = config.get(prop)
            prop_defaults = defaults.get(prop)
            if prop == "globals":
                # Keep the deep copy made above -- rebuilding it here would
                # undo the isolation, and would drop `_`-prefixed entries,
                # which in globals are ordinary variable names rather than the
                # collection ids that rule is meant for.
                _value = netboot.globals
            # `isinstance(origin, type)` first: a union annotation's origin is
            # not a class at all on 3.9, and `issubclass` raises on it.
            elif isinstance(origin, type) and issubclass(origin, dict):
                value: dict[str] = {} if value is None else value
                if not isinstance(value, Mapping):
                    raise PixieConfigError(
                        f"{prop}: expected a mapping of id to entry, got "
                        f"{type(value).__name__}"
                    )
                _keycls, _valcls = _ty.get_args(hint)
                _known = None if _valcls is object else _config_keys(_valcls)
                if _known is not None and isinstance(prop_defaults, Mapping):
                    _warn_unknown_keys(f"defaults.{prop}.", prop_defaults, _known)
                _value = {}
                if _valcls is object:
                    _valctr = lambda _id, val: val
                else:

                    def _valctr(_id, val):
                        # `targets: {host1:}` is valid YAML and means None; it
                        # is also the natural way to write "this entry, all
                        # defaults", so treat it as an empty mapping instead of
                        # failing on `None.__dict__`.
                        if val is None:
                            val = {}
                        elif not isinstance(val, Mapping):
                            fields = getattr(val, "__dict__", None)
                            if fields is None:
                                raise PixieConfigError(
                                    f"{prop}.{_id}: expected a mapping, got "
                                    f"{type(val).__name__} ({val!r})"
                                )
                            val = fields
                        if _known is not None:
                            _warn_unknown_keys(f"{prop}.{_id}.", val, _known)
                        if prop_defaults:
                            _val = deepcopy(prop_defaults)
                            _val.update(val)
                            val = _val
                        if isinstance(_valcls, type) and issubclass(_valcls, DHCPZone):
                            val = dict(val, codes=netboot.option_codes)
                        try:
                            return _valcls(_id=_id, **val)
                        except TypeError as exc:
                            # Only "this class takes no _id" justifies a retry;
                            # any other TypeError is the value class's own and
                            # must not be hidden behind a second construction.
                            if "_id" not in str(exc):
                                raise
                            return _valcls(**val)

                for uid, val in value.items():
                    if not isinstance(uid, str):
                        raise PixieConfigError(
                            f"{prop}: the id {uid!r} is not text "
                            f"({type(uid).__name__}); quote it (YAML reads an unquoted "
                            "52:54:00:12:34:56 as a number, and yes as a boolean)"
                        )
                    if uid.startswith("_"):
                        continue
                    try:
                        _value[_keycls(uid)] = _valctr(uid, val)
                    except PixieError:
                        raise
                    except ValueError as exc:  # a MAC, address or network
                        raise PixieConfigError(f"{prop}.{uid}: {exc}") from exc
            elif value is None:
                # Leave a class-level default in place instead of overwriting
                # it with None just because the config does not mention it.
                _value = getattr(netboot, prop, None)
            else:
                # Coerce through the annotation when it is a real constructor.
                # `Optional[str]` is not one: under PEP 604 its origin is
                # `types.UnionType`, which passes `isinstance(..., type)` and
                # then refuses to be called.
                try:
                    _value = origin(value) if isinstance(origin, type) else value
                except TypeError:
                    _value = value
            prop, value = netboot.hook(
                PixieEvent.SetPixieProperty,
                (prop, _value),
                origin=origin,
                rawvalue=value,
            )
            setattr(netboot, prop, value)

        netboot.hook(PixieEvent.PixieInitiated)

    def _match_target(self, query: str) -> "PixieTarget|None":
        """Find the target a user means by id, hostname, MAC or IP.

        An exact match anywhere in the table beats a prefix match, and an
        ambiguous query raises instead of picking whichever entry comes first:
        guessing here arms PXE on a machine nobody asked for. MAC input is
        parsed, so colon, hyphen and Cisco-dot spellings all match.
        """
        if not query:
            return None
        exact = self.targets.get(query, None)
        if exact is not None:
            return exact

        lower = query.lower()
        mac = netimps.try_parse(query, MACAddress)
        if mac is not None and str(mac) == PixieTarget._NULL_MAC:
            mac = None  # the "unset" MAC must not match every MAC-less target
        ip = netimps.try_parse(query, IPAddress)

        def exact():
            return [
                candidate
                for candidate in self.targets.values()
                if candidate.hostname.lower() == lower
                or (mac is not None and candidate.mac == mac)
                or (ip is not None and candidate.ip == ip)
            ]

        matches = exact()
        if not matches and ip is not None:
            # The address may be a hostname-only target's: look those up now.
            for candidate in self.targets.values():
                candidate.resolve_ip()
            matches = exact()
        if not matches:
            matches = [
                candidate
                for candidate in self.targets.values()
                if candidate.hostname and candidate.hostname.lower().startswith(lower)
            ]
        if len(matches) > 1:
            raise PixieLookupError(
                f"ambiguous target {query!r}: matches "
                f"{sorted(str(candidate._id) for candidate in matches)}"
            )
        return matches[0] if matches else None

    def lookup_target(self, target: str) -> "PixieTarget|None":
        """Find the target a user named, or `None`.

        Exact id first, then an exact hostname/MAC/IP match anywhere in the table,
        then a unique hostname prefix. Raises `PixieLookupError` rather than
        guessing when a query matches several targets.
        """
        target: Union[str, PixieTarget] = self.hook(PixieEvent.LookupTarget, target)
        if isinstance(target, str):
            _target = self._match_target(target)
            if _target is not None:
                target = _target
        if isinstance(target, PixieTarget):
            target.resolve_ip()

        target = self.hook(PixieEvent.FoundTarget, target)
        if not isinstance(target, PixieTarget):
            target = None
        return target

    def lookup_image(
        self, name: str, target: "PixieTarget | None" = None
    ) -> "PixieImage | None":
        """The best-matching image, or `None` when none matches.

        `match` may return a bool or a comparable score; anything falsy --
        `False`, `None`, `0`, an empty `re.Match`-less result -- means "not
        this one". Scores are compared against each other, so a `match`
        returning a non-comparable type raises rather than silently ordering
        by chance.
        """
        best_score, best_image = None, None
        for img_name, image in self.images.items():
            score = image.match(img_name, name)
            if not score:
                continue
            if best_score is None or score > best_score:
                best_score, best_image = score, image
        return self.hook(PixieEvent.FoundTargetImage, best_image, target=target)

    def lookup_dhcpzone(self, name: str, target: PixieTarget = None) -> "DHCPZone|None":
        """The zone for `name`, or the most specific one containing the target's IP.

        The resolved id is cached back onto `target.dhcpzone`.
        """
        if not name and target:
            if target.dhcpzone:
                name = target.dhcpzone
            elif target.resolve_ip().ip:
                # Containment needs a real address: a MAC-keyed target whose
                # hostname never resolved has `ip == ""`, and `"" in network`
                # raises AttributeError rather than returning False.
                # The most specific containing zone wins: with 10.0.0.0/16 and
                # 10.0.5.0/24 configured, a host in the /24 belongs to the /24
                # whichever order the zones happen to be declared in.
                containing = [
                    (zone.network.prefixlen, zone_id)
                    for zone_id, zone in self.dhcpzones.items()
                    if zone.network and target.ip in zone.network
                ]
                if containing:
                    name = max(containing)[1]
                    target.dhcpzone = name
        zone = self.dhcpzones.get(name, None)
        return self.hook(PixieEvent.FoundTargetDhcpzone, zone, target=target)

    def make_context(
        self,
        target: "PixieTarget",
        globals: list[dict] = None,
        require_image: bool = True,
    ) -> PixieContext:
        """Build the render context for `target`.

        Resolves the image and zone, layers the globals (engine, then image, zone
        and target), and attaches a renderer over the configured template roots.
        `require_image=False` tolerates an image that is no longer configured, so
        cleanup can still run.
        """
        target.resolve_ip()
        image = self.lookup_image(target.image, target)
        if not image and not require_image:
            # `complete` only needs the zone, to disarm DHCP. Refusing to clean
            # up a machine because its image was retired from the config leaves
            # it armed forever.
            LOGGER.warning(
                "target %s: image %r is not configured; continuing without it",
                target._id,
                target.image,
            )
            image = PixieImage(_id=target.image or "")
        elif not image:
            known = ", ".join(sorted(str(i) for i in self.images)) or "none configured"
            raise PixieLookupError(
                f"target {target._id!r} wants image {target.image!r}, which no "
                f"configured image matches (images: {known})"
            )
        LOGGER.info(f"Found target image: {target.image}")

        zone = self.lookup_dhcpzone(target.dhcpzone, target)
        if not zone:
            known = (
                ", ".join(sorted(str(z) for z in self.dhcpzones)) or "none configured"
            )
            wanted = target.dhcpzone or (
                f"a zone containing {target.ip}"
                if target.ip
                else "a zone, but the target has neither a dhcpzone nor an ip"
            )
            raise PixieLookupError(
                f"target {target._id!r} needs {wanted} (zones: {known})"
            )
        LOGGER.info(f"Found target dhcpzone: {target.dhcpzone}")

        ctx = {
            "image": image,
            "dhcpzone": zone,
            "target": target,
            "repos": self.repos,
            "resources": {},
        }
        # Collect object-level globals to layer into the context WITHOUT mutating
        # the shared image/dhcpzone/target objects (they are reused across
        # targets, so deleting their `globals` would drop them for later calls).
        _globals = [self.globals, *(globals or [])]
        for k in ["image", "dhcpzone", "target"]:
            g = getattr(ctx[k], "globals", None)
            if g:
                _globals.append(g)
        _globals = [self._without_reserved_keys(g) for g in _globals]

        ctx = mergeObjects(self._ctxcls, ctx, *_globals)

        ctx: PixieContext
        undefined = self.templates_undefined
        if undefined not in UNDEFINED_MODES:
            raise PixieConfigError(
                f"templates_undefined must be one of {', '.join(UNDEFINED_MODES)}, "
                f"not {undefined!r}"
            )
        declared = self._config.get("templates") or []
        if not isinstance(declared, (list, tuple)):
            declared = [declared]  # `templates: dir` is one root, not one per letter
        roots = [
            root if isinstance(root, pathapi.Path) else parse_path(str(root))
            for root in declared
        ]
        ctx._loader = tplx.Loader(
            roots,
            undefined=undefined,
            globals={
                "shell_quote": shell_quote,
                "Path": pathapi.Path,
                "Uri": URIPath,
            },
            # `_id` names an image or a target, and templates use it.
            encoder=tplx.DataEncoder(expose=["_id"]),
        )
        ctx.version = f"netboot-v{self.VERSION}"
        # `netboot.dhcp` fires BuildDhcpOptions through the engine while building
        # a reservation, and it only has the context.
        ctx._netboot_ = self
        return self.hook(PixieEvent.PixieContextForTarget, ctx, target=target)

    def _without_reserved_keys(self, values: dict) -> dict:
        """Drop globals that would overwrite the context's own fields.

        A global named `target` or `version` would replace the context's own
        value, and one named `render` or `searchpaths` its method: silently,
        and only for the targets whose globals happen to carry that name.
        """
        if not isinstance(values, Mapping):
            return values
        cls = self._ctxcls
        reserved = _config_keys(cls) | {n for n in dir(cls) if not n.startswith("_")}
        clashes = reserved.intersection(values)
        if not clashes:
            return values
        LOGGER.warning(
            "ignoring global(s) %s: those names are the render context's own fields",
            ", ".join(sorted(clashes)),
        )
        return {k: v for k, v in values.items() if k not in clashes}

    def initialize(self, target: "PixieTarget", **options: object) -> PixieContext:
        """Arm DHCP for `target` and return its context.

        A backend that fails is a warning; it raises only if none could be armed.
        `options` (`initiate`'s `iscsi=`) reach the Start/EndPixieInitialize
        hooks as their keyword arguments.
        """
        target = self.hook(PixieEvent.StartPixieInitialize, target, **options)
        ctx = self.make_context(target)
        ctx = ctx.pxe_init(self)
        return self.hook(PixieEvent.EndPixieInitialize, ctx, **options)

    def complete(self, target: "PixieTarget", **options: object) -> PixieContext:
        """Disarm DHCP for `target` and return its context.

        Every backend is tried; one that fails is logged as a warning.
        `options` reach the Start/EndPixieComplete hooks as keyword arguments.
        """
        target = self.hook(PixieEvent.StartPixieComplete, target, **options)
        ctx = self.make_context(target, require_image=False)
        ctx = ctx.pxe_complete(self)
        return self.hook(PixieEvent.EndPixieComplete, ctx, **options)
