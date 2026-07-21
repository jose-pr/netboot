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

from . import _netutils as netutils
from pathlib_next.uri.schemes import *  # noqa: F401,F403
from yaconfiglib import OpaqueMerge
from yaconfiglib import typed_merge as mergeObjects

from .content import Repository, Resource
from .dhcp import DhcpZone
from .logging import LOGGER
from .templates import Loader, Renderer
from .utils import IPAddress, MACAddress, T
from .utils.misc import import_


class NetbootTarget(Namespace, OpaqueMerge):
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
        resolve = not self.ip or not self.hostname
        while resolve:
            resolve = False
            not_mac = not MACAddress._VALID_MAC.match(self._id)
            id_is_ip = netutils.is_valid_ip(self._id)
            if not self.ip and self.hostname:
                _resolved = netutils.nslookup(self.hostname)
                if _resolved:
                    self.ip = _resolved[0]
                resolve = True
            if not self.ip and id_is_ip:
                self.ip = self._id
                resolve = True
            if not self.hostname and not_mac and not id_is_ip:
                self.hostname = self._id
                resolve = True
        if self.ip:
            self.ip = netutils.parse_ip(self.ip)
        self.hostname = self.hostname.lower()


class NetbootImage(Resource):
    template_path: list["Path"]
    globals: dict

    def match(self, name: str, check: str):
        return name == check


class NetbootContext(Namespace):
    target: NetbootTarget
    image: NetbootImage
    dhcpzone: DhcpZone
    repos: dict[str, Repository]
    generated: datetime.datetime
    resources: dict[str, Resource]
    version: str
    templates: list[Union[str, Path]]
    _renderer: Renderer

    def __init__(self, **kwargs) -> None:
        self.dhcp_server = None
        self.generated = datetime.datetime.now()
        super().__init__(**kwargs)

    def init(self, netboot: "Netboot"): ...

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

    def pxe_init(self, config: "Netboot"):
        for dhcpserver in self.dhcpzone.dhcpservers:
            dhcpserver.add_target(self)
        return self

    def pxe_complete(self, config: "Netboot"):
        for dhcpserver in self.dhcpzone.dhcpservers:
            dhcpserver.remove_target(self)
        return self

    def _template_names(self, suffix: Union[list[str], str], **options) -> list[str]:
        # ``options`` (a ``k=v:name`` template spec) is accepted for forward
        # compatibility; name selection does not use it today.
        suffixes = suffix if isinstance(suffix, list) else [suffix]
        ip = self.target.ip
        # Skip an unset/unspecified IP so it never yields a spurious name.
        ip_name = str(ip) if ip and str(ip) not in ("0.0.0.0", "::") else ""
        names = []
        for version in [
            self.target.mac.as_str("-"),
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
        # Each NetbootContext owns its own Renderer (built in make_context), so
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


class NetbootEvent(StrEnum):
    NewNetbootObject = "NetbootEvent.NewNetbootObject"
    StartNetbootInit = "NetbootEvent.StartNetbootInit"
    SetNetbootProperty = "NetbootEvent.SetNetbootProperty"
    NetbootInitiated = "NetbootEvent.NetbootInitiated"
    LookupTarget = "NetbootEvent.LookupTarget"
    FoundTarget = "NetbootEvent.FoundTarget"
    FoundTargetImage = "NetbootEvent.FoundTargetImage"
    FoundTargetDhcpzone = "NetbootEvent.FoundTargetDhcpzone"
    NetbootContextForTarget = "NetbootEvent.NetbootContextForTarget"
    StartNetbootInitialize = "NetbootEvent.StartNetbootInitialize"
    EndNetbootInitialize = "NetbootEvent.EndNetbootInitialize"
    StartNetbootComplete = "NetbootEvent.StartNetbootComplete"
    EndNetbootComplete = "NetbootEvent.EndNetbootComplete"


_NetbootHook = _ty.Callable[["NetbootEvent", "Netboot", T, dict], T]


class Netboot:
    targets: "dict[str,NetbootTarget]"
    dhcpzones: "dict[str,DhcpZone]"
    images: "dict[str, NetbootImage]"
    repos: "dict[str,Repository]"
    globals: dict[str, object]
    _ctxcls: NetbootContext = NetbootContext
    VERSION = __version__
    _config: dict
    _hooks: list[_NetbootHook] = []

    def hook(
        self: "Netboot|_ty.Sequence[_NetbootHook]",
        event: NetbootEvent,
        __value: T = None,
        /,
        **kwargs,
    ):
        LOGGER.debug(f"Running Hooks for: {event}")
        if isinstance(self, Netboot):
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
        hooks: _ty.Sequence[_NetbootHook] = [],
        **config,
    ):

        hooks = [(hook if callable(hook) else import_(hook)) for hook in hooks]
        cls = Netboot.hook(hooks, NetbootEvent.NewNetbootObject, cls, config=config)
        inst = object.__new__(cls)
        inst._hooks = hooks
        return inst

    def __init__(
        netboot,
        /,
        **config,
    ):
        netboot._config = netboot.hook(NetbootEvent.StartNetbootInit, config)
        netboot.globals = deepcopy(config.get("globals") or {})
        defaults = config.get("defaults") or {}

        for prop, hint in get_type_hints(netboot.__class__).items():
            if prop.startswith("_"):
                continue
            origin = _ty.get_origin(hint) or hint
            value = config.get(prop)
            prop_defaults = defaults.get(prop)
            if issubclass(origin, dict):
                value: dict[str] = value or {}
                _keycls, _valcls = _ty.get_args(hint)
                _value = {}
                if _valcls is object:
                    _valctr = lambda _id, val: val
                else:

                    def _valctr(_id, val):
                        val = val if isinstance(val, Mapping) else val.__dict__
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
                NetbootEvent.SetNetbootProperty, (prop, _value), origin=origin, rawvalue=value
            )
            setattr(netboot, prop, value)

        netboot.hook(NetbootEvent.NetbootInitiated)

    def lookup_target(self, target: str) -> NetbootTarget:
        target: Union[str, NetbootTarget] = self.hook(NetbootEvent.LookupTarget, target)
        if isinstance(target, str):
            lower: str = target.lower()
            _target = self.targets.get(target, None)
            if _target is None:
                for _target in self.targets.values():
                    if (
                        _target.hostname.lower().startswith(lower)
                        or _target.mac.as_str() == lower
                        or str(_target.ip) == lower
                    ):
                        target = _target
                        break

            else:
                target = _target

        target = self.hook(NetbootEvent.FoundTarget, target)
        if not isinstance(target, NetbootTarget):
            target = None
        return target

    def lookup_image(self, name: str, target: NetbootTarget = None) -> NetbootImage:
        imgs: list[tuple[int, NetbootImage]] = [(-1, {})]
        for img_name, image in self.images.items():
            check = image.match(img_name, name)
            if check != False:
                imgs.append((check, image))
        imgs.sort(key=lambda x: x[0])
        return self.hook(NetbootEvent.FoundTargetImage, imgs.pop()[1], target=target)

    def lookup_dhcpzone(self, name: str, target: NetbootTarget = None) -> DhcpZone:
        if not name and target:
            if target.dhcpzone:
                name = target.dhcpzone
            else:
                for zone_id, zone in self.dhcpzones.items():
                    if target.ip in zone.network:
                        name = zone_id
                        target.dhcpzone = zone_id
                        break
        zone = self.dhcpzones.get(name, None)
        return self.hook(NetbootEvent.FoundTargetDhcpzone, zone, target=target)

    def make_context(
        self, target: "NetbootTarget", globals: list[dict] = None
    ) -> NetbootContext:
        image = self.lookup_image(target.image, target)
        if not image:
            raise Exception(f"Image not found for target: {target.image}")
        LOGGER.info(f"Found target image: {target.image}")

        zone = self.lookup_dhcpzone(target.dhcpzone, target)
        if not zone:
            raise Exception(
                f"Not supported client due to missing subnet: {target.dhcpzone}"
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

        ctx = mergeObjects(self._ctxcls, ctx, *_globals)

        ctx: NetbootContext
        ctx._renderer = Renderer(loader=Loader(self._config.get("templates", [])))
        ctx.version = f"netboot-v{self.VERSION}"
        return self.hook(NetbootEvent.NetbootContextForTarget, ctx, target=target)

    def initialize(self, target: "NetbootTarget"):
        target = self.hook(NetbootEvent.StartNetbootInitialize, target)
        ctx = self.make_context(target)
        ctx = ctx.pxe_init(self)
        return self.hook(NetbootEvent.EndNetbootInitialize, ctx)

    def complete(self, target: "NetbootTarget"):
        target = self.hook(NetbootEvent.StartNetbootComplete, target)
        ctx = self.make_context(target)
        ctx = ctx.pxe_complete(self)
        return self.hook(NetbootEvent.EndNetbootComplete, ctx)
