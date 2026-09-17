import datetime
import enum as _enum
import typing as _ty
from copy import deepcopy
from pathlib import Path

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("netboot")
    except PackageNotFoundError:  # running from a source tree without install
        __version__ = "0.0.0"
except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.9+
    __version__ = "0.0.0"

# Compat: StrEnum introduced in Python 3.11; emulate for older Pythons
try:
    from enum import StrEnum
except ImportError:

    class StrEnum(str, _enum.Enum):
        def __str__(self):
            return self.value


from argparse import Namespace
from typing import Mapping, get_type_hints, Union

from .utils import net as netutils
from pathlib_next.uri.schemes import *  # noqa: F401,F403
from yaconfiglib import OpaqueMerge
from yaconfiglib import typed_merge as mergeObjects

from .content import Repository, Resource
from .dhcp import DhcpZone
from .logging import LOGGER
from .templates import Loader, Renderer
from .utils import IPAddress, MACAddress, T
from .utils.misc import import_


class PixieError(Exception):
    """Base for the errors netboot raises on purpose.

    Separates "the operator gave us something unusable" from "netboot has a
    bug": the CLI reports the former as a one-line message and keeps the
    traceback for the latter.
    """


class PixieLookupError(PixieError, LookupError):
    """A target, image or zone could not be resolved, or the query was ambiguous."""


class PixieConfigError(PixieError, ValueError):
    """The configuration cannot be used as given."""


class PixieTarget(Namespace, OpaqueMerge):
    _id: str
    hostname: str
    ip: IPAddress
    mac: MACAddress
    image: str
    dhcpzone: str
    globals: dict
    template_path: list[Union[str, Path]]

    #: MAC value treated as "unset" (a target keyed by hostname/IP has no MAC).
    _NULL_MAC = "00:00:00:00:00:00"

    def __init__(self, **kwargs) -> None:
        for prop, key in {
            "dhcpzone": "",
            "mac": "",
            "ip": "",
            "image": "",
            "hostname": "",
            "template_path": [],
        }.items():
            kwargs.setdefault(prop, key)
        super().__init__(**kwargs)
        # If no MAC was given but the id itself is a MAC, adopt it; otherwise
        # fall back to the null MAC so downstream `.mac` is always a MACAddress.
        if not self.mac or str(self.mac) == self._NULL_MAC:
            if MACAddress._VALID_MAC.match(str(self._id)):
                self.mac = self._id
            else:
                self.mac = self._NULL_MAC
        if not isinstance(self.mac, MACAddress):
            self.mac = MACAddress(self.mac)
        # Fill in whatever the id itself tells us, then resolve at most once.
        # This is deliberately not a loop: `resolve()` answers [] for a name
        # that does not resolve, so retrying asks the same question forever --
        # and every target is built at startup, so one unresolvable entry used
        # to hang every command.
        id_str = str(getattr(self, "_id", "") or "")
        id_is_mac = bool(MACAddress._VALID_MAC.match(id_str))
        id_is_ip = netutils.is_valid(id_str, netutils.IPAddress)
        if not self.ip and id_is_ip:
            self.ip = id_str
        if not self.hostname and not id_is_mac and not id_is_ip:
            self.hostname = id_str
        if not self.ip and self.hostname:
            try:
                _resolved = netutils.resolve(self.hostname)
            except ValueError as exc:  # malformed name: not worth killing the run
                _resolved = None
                LOGGER.warning(
                    "target %s: %r is not resolvable: %s", id_str, self.hostname, exc
                )
            if _resolved:
                self.ip = _resolved[0]
            elif _resolved is not None:
                LOGGER.warning(
                    "target %s: hostname %r did not resolve; its ip stays unset",
                    id_str,
                    self.hostname,
                )
        if self.ip:
            self.ip = netutils.try_parse(self.ip, netutils.IPAddress)
        self.hostname = self.hostname.lower()


class PixieImage(Resource):
    template_path: list["Path"]
    globals: dict

    def __init__(self, **kwargs) -> None:
        # `searchpaths` reads this on every render, so an image that simply
        # does not declare one must still have the empty list.
        kwargs.setdefault("template_path", [])
        super().__init__(**kwargs)

    def match(self, name: str, check: str):
        return name == check


class PixieContext(Namespace):
    target: PixieTarget
    image: PixieImage
    dhcpzone: DhcpZone
    repos: dict[str, Repository]
    generated: datetime.datetime
    resources: dict[str, Resource]
    version: str
    _renderer: Renderer

    def __init__(self, **kwargs) -> None:
        self.dhcp_server = None
        self.generated = datetime.datetime.now()
        super().__init__(**kwargs)

    def init(self, netboot: "Pixie"): ...

    def resource(self, name: Union[str, Resource], service: str = None):
        if isinstance(name, Resource):
            repo = self.repos.get(name.src, None)
            path = name.path
        else:
            path = None
            repo = self.resource_repo(name)
        if not repo:
            return
        if not path:
            path = self.resources[name].path
        return repo.get(path, service=service)

    def resource_repo(self, name: str):
        resource = self.resources.get(name)
        if not resource:
            return
        return self.repos.get(resource.src)

    def pxe_init(self, config: "Pixie"):
        """Arm every DHCP backend for this target, all or nothing.

        A target armed on two of three backends is worse than one armed on
        none: it may boot into an installer from one server while another
        hands out its normal lease. So a failure rolls the already-armed
        backends back before re-raising.
        """
        armed = []
        for dhcpserver in self.dhcpzone.dhcpservers:
            try:
                dhcpserver.add_target(self)
            except Exception:
                for done in reversed(armed):
                    try:
                        done.remove_target(self)
                    except Exception:  # keep unwinding; report the first cause
                        LOGGER.exception(
                            "rollback failed for %s on %s",
                            self.target._id,
                            getattr(done, "uri", done),
                        )
                raise
            armed.append(dhcpserver)
        return self

    def pxe_complete(self, config: "Pixie"):
        """Disarm every DHCP backend, continuing past a failure.

        Cleanup is the opposite case from arming: stopping at the first error
        would leave the remaining backends armed, so every backend is tried and
        the first error is raised once they have all had their turn.
        """
        first_error = None
        for dhcpserver in self.dhcpzone.dhcpservers:
            try:
                dhcpserver.remove_target(self)
            except Exception as exc:
                LOGGER.exception(
                    "could not disarm %s on %s",
                    self.target._id,
                    getattr(dhcpserver, "uri", dhcpserver),
                )
                first_error = first_error or exc
        if first_error is not None:
            raise first_error
        return self

    def _template_names(self, suffix: Union[list[str], str], **options) -> list[str]:
        # ``options`` (a ``k=v:name`` template spec) is accepted for forward
        # compatibility; name selection does not use it today.
        suffixes = suffix if isinstance(suffix, list) else [suffix]
        ip = self.target.ip
        # Skip unset values so they never yield a spurious name. The null MAC
        # is the same case as the unspecified IP: every MAC-less target would
        # otherwise share the candidate `00-00-00-00-00-00.<name>`, and one
        # stray file of that name would apply to all of them.
        ip_name = str(ip) if ip and str(ip) not in ("0.0.0.0", "::") else ""
        mac = self.target.mac
        mac_name = mac.as_str("-") if str(mac) != PixieTarget._NULL_MAC else ""
        names = []
        for version in [
            mac_name,
            self.target.hostname,
            ip_name,
        ]:
            if version:
                for suffix in suffixes:
                    names.append(f"{version}.{suffix}")
        for suffix in suffixes:
            names.append(suffix)

        return names

    @property
    def searchpaths(self) -> list[Path]:
        return [*self.target.template_path, *self.image.template_path]

    def render(self, filename: str, strict=True):
        # Each PixieContext owns its own Renderer (built in make_context), so
        # setting globals["ctx"] here is per-context; do not share one Renderer
        # across contexts or nested renders would clobber this.
        self._renderer.globals["ctx"] = self
        try:
            template = self._renderer.get_template(filename)
            return template.render()
        except Exception as e:
            if strict:
                raise e
            else:
                LOGGER.debug(
                    f"While rendering [{filename}] encountered an exeption:\t{repr(e)}"
                )
                return None


class PixieEvent(StrEnum):
    NewPixieObject = "PixieEvent.NewPixieObject"
    StartPixieInit = "PixieEvent.StartPixieInit"
    SetPixieProperty = "PixieEvent.SetPixieProperty"
    PixieInitiated = "PixieEvent.PixieInitiated"
    LookupTarget = "PixieEvent.LookupTarget"
    FoundTarget = "PixieEvent.FoundTarget"
    FoundTargetImage = "PixieEvent.FoundTargetImage"
    FoundTargetDhcpzone = "PixieEvent.FoundTargetDhcpzone"
    PixieContextForTarget = "PixieEvent.PixieContextForTarget"
    StartPixieInitialize = "PixieEvent.StartPixieInitialize"
    EndPixieInitialize = "PixieEvent.EndPixieInitialize"
    StartPixieComplete = "PixieEvent.StartPixieComplete"
    EndPixieComplete = "PixieEvent.EndPixieComplete"


_PixieHook = _ty.Callable[["PixieEvent", "Pixie", T, dict], T]


class Pixie:
    targets: "dict[str,PixieTarget]"
    dhcpzones: "dict[str,DhcpZone]"
    images: "dict[str, PixieImage]"
    repos: "dict[str,Repository]"
    globals: dict[str, object]
    _ctxcls: PixieContext = PixieContext
    VERSION = __version__
    _config: dict
    _hooks: list[_PixieHook] = []

    def hook(
        self: "Pixie|_ty.Sequence[_PixieHook]",
        event: PixieEvent,
        __value: T = None,
        /,
        **kwargs,
    ):
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
        hooks: _ty.Sequence[_PixieHook] = [],
        **config,
    ):

        hooks = [(hook if callable(hook) else import_(hook)) for hook in hooks]
        cls = Pixie.hook(hooks, PixieEvent.NewPixieObject, cls, config=config)
        inst = object.__new__(cls)
        inst._hooks = hooks
        return inst

    def __init__(
        netboot,
        /,
        **config,
    ):
        netboot._config = netboot.hook(PixieEvent.StartPixieInit, config)
        netboot.globals = deepcopy(config.get("globals") or {})
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
            elif issubclass(origin, dict):
                value: dict[str] = value or {}
                _keycls, _valcls = _ty.get_args(hint)
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
                        if prop_defaults:
                            _val = deepcopy(prop_defaults)
                            _val.update(val)
                            val = _val
                        try:
                            return _valcls(_id=_id, **val)
                        except TypeError:
                            # _valcls doesn't accept an _id kwarg; build without.
                            return _valcls(**val)

                for uid, val in value.items():
                    if not str(uid).startswith("_"):
                        _value[_keycls(uid)] = _valctr(uid, val)
            else:
                _value = hint(value) if value is not None else None
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
        mac = netutils.try_parse(query, MACAddress)
        if mac is not None and str(mac) == PixieTarget._NULL_MAC:
            mac = None  # the "unset" MAC must not match every MAC-less target
        ip = netutils.try_parse(query, netutils.IPAddress)
        matches = [
            candidate
            for candidate in self.targets.values()
            if candidate.hostname.lower() == lower
            or (mac is not None and candidate.mac == mac)
            or (ip is not None and candidate.ip == ip)
        ]
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

    def lookup_target(self, target: str) -> PixieTarget:
        target: Union[str, PixieTarget] = self.hook(PixieEvent.LookupTarget, target)
        if isinstance(target, str):
            _target = self._match_target(target)
            if _target is not None:
                target = _target

        target = self.hook(PixieEvent.FoundTarget, target)
        if not isinstance(target, PixieTarget):
            target = None
        return target

    def lookup_image(self, name: str, target: PixieTarget = None) -> PixieImage:
        """The best-matching image, or a falsy `{}` when none matches.

        `match` may return a bool or a comparable score; anything falsy --
        `False`, `None`, `0`, an empty `re.Match`-less result -- means "not
        this one". Scores are compared against each other, so a `match`
        returning a non-comparable type raises rather than silently ordering
        by chance.
        """
        best_score, best_image = None, {}
        for img_name, image in self.images.items():
            score = image.match(img_name, name)
            if not score:
                continue
            if best_score is None or score > best_score:
                best_score, best_image = score, image
        return self.hook(PixieEvent.FoundTargetImage, best_image, target=target)

    def lookup_dhcpzone(self, name: str, target: PixieTarget = None) -> DhcpZone:
        if not name and target:
            if target.dhcpzone:
                name = target.dhcpzone
            elif target.ip:
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

    #: Context fields a global must never replace: they are the context.
    _RESERVED_CONTEXT_KEYS = frozenset(
        {"image", "dhcpzone", "target", "repos", "resources"}
    )

    def make_context(
        self,
        target: "PixieTarget",
        globals: list[dict] = None,
        require_image: bool = True,
    ) -> PixieContext:
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
        ctx._renderer = Renderer(
            loader=Loader(self._config.get("templates", [])),
            # Boot artifacts are line-oriented files: a kickstart or iPXE
            # script whose last line lost its newline is a different file. The
            # shell engine already kept it, so the two engines disagreed.
            keep_trailing_newline=True,
        )
        ctx.version = f"netboot-v{self.VERSION}"
        return self.hook(PixieEvent.PixieContextForTarget, ctx, target=target)

    def _without_reserved_keys(self, values: dict) -> dict:
        """Drop globals that would overwrite the context's own fields.

        A global named `target` or `image` would replace the engine object the
        templates are rendered against -- silently, and only for the targets
        whose globals happen to carry that name.
        """
        if not isinstance(values, Mapping):
            return values
        clashes = self._RESERVED_CONTEXT_KEYS.intersection(values)
        if not clashes:
            return values
        LOGGER.warning(
            "ignoring global(s) %s: those names are the render context's own fields",
            ", ".join(sorted(clashes)),
        )
        return {k: v for k, v in values.items() if k not in clashes}

    def initialize(self, target: "PixieTarget"):
        target = self.hook(PixieEvent.StartPixieInitialize, target)
        ctx = self.make_context(target)
        ctx = ctx.pxe_init(self)
        return self.hook(PixieEvent.EndPixieInitialize, ctx)

    def complete(self, target: "PixieTarget"):
        target = self.hook(PixieEvent.StartPixieComplete, target)
        ctx = self.make_context(target, require_image=False)
        ctx = ctx.pxe_complete(self)
        return self.hook(PixieEvent.EndPixieComplete, ctx)
