import typing as _ty
from argparse import Namespace

from . import _netutils as netutils
from yaconfiglib import OpaqueMerge

from .utils.net import IPAddress, IPInterface, IPNetwork

if _ty.TYPE_CHECKING:
    from . import PixyContext

from urllib.parse import urlparse


def _iter_subclasses(cls: type) -> "_ty.Iterator[type]":
    """Yield every subclass of ``cls``, recursively (not just direct children).

    Plugin ``DhcpServer`` handlers loaded via ``--load-module`` may subclass an
    intermediate base, so a one-level ``__subclasses__()`` scan would miss them.
    """
    for sub in cls.__subclasses__():
        yield sub
        yield from _iter_subclasses(sub)


class DhcpServer:
    """Base for DHCP backends. ``DhcpServer(uri)`` dispatches on the URI scheme.

    A concrete backend is a subclass whose lowercased class name matches the URI
    scheme (e.g. ``class dnsmasq(DhcpServer)`` handles ``dnsmasq://...``); such
    backends are typically provided by a plugin module imported via
    ``--load-module``. Subclassing at any depth is honoured.
    """

    DEFAULT_SCHEME = None

    def __new__(cls, uri: str):
        if cls is DhcpServer:
            parsed = urlparse(uri)
            scheme = parsed.scheme or cls.DEFAULT_SCHEME
            for sub in _iter_subclasses(cls):
                if sub.__name__.lower() == scheme:
                    return object.__new__(sub)
            raise ValueError(
                f"No DhcpServer backend registered for scheme {scheme!r} "
                f"(uri={uri!r}); import a plugin module providing it via --load-module"
            )
        return object.__new__(cls)

    def __init__(self, uri: str):
        self.uri = uri

    def remove_target(self, pixy: "PixyContext"):
        pass

    def add_target(self, pixy: "PixyContext"):
        pass


class DhcpZone(Namespace, OpaqueMerge):
    network: IPNetwork
    gateway: "_ty.Optional[IPAddress]"
    domain: "_ty.Optional[str]"
    search: "list[str]"
    nameservers: "list[IPAddress]"
    globals: dict
    dhcpservers: "list[DhcpServer]"

    @property
    def nameserver(self):
        return self.nameservers[0] if self.nameservers else ""

    def get_local_server(self, servers: "list[IPAddress]", default: IPAddress):
        for server in servers:
            if server in self.network:
                return server
        return default

    def __init__(self, **kwargs) -> None:
        _gateway = kwargs.get("gateway")
        _network = kwargs.get("network")
        _netmask = kwargs.get("netmask")
        if _gateway and not _network:
            gw: netutils.IPv4Interface = IPInterface(_gateway)
            if not _netmask and isinstance(_gateway, str) and "/" in _gateway:
                _netmask = gw.netmask.exploded
                kwargs["gateway"] = gw.ip.exploded
            if _netmask:
                _network = gw.network.network_address.exploded

        if isinstance(_network, str) and "/" in _network:
            net = IPNetwork(_network)
            _network = net.network_address.exploded
            _netmask = net.netmask.exploded
        if _network:
            if _netmask and isinstance(_network, str):
                _network = f"{_network}/{_netmask}"
            kwargs["network"] = _network

        kwargs.pop("netmask", None)
        kwargs.setdefault("dhcpservers", [])
        super().__init__(**kwargs)
        self.dhcpservers = [
            server if isinstance(server, DhcpServer) else DhcpServer(server)
            for server in (self.dhcpservers or [])
        ]
        self.network = netutils.parse_network(self.network)
        for prop, ctr in [("nameservers", netutils.parse_ip), ("search", str)]:
            value = getattr(self, prop, None)
            if not value:
                setattr(self, prop, [])
            elif not isinstance(value, list):
                setattr(self, prop, [value])
            vals = getattr(self, prop)
            for i, val in enumerate(vals):
                vals[i] = ctr(val)

        for prop in ["gateway", "domain"]:
            if not getattr(self, prop, None):
                setattr(self, prop, None)

        if self.gateway:
            self.gateway = netutils.parse_ip(self.gateway)
