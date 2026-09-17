import importlib as _importlib
import typing as _ty
from argparse import Namespace

from ..utils import net as netutils
from yaconfiglib import OpaqueMerge

from ..logging import LOGGER
from ..utils.net import IPAddress, IPInterface, IPNetwork

if _ty.TYPE_CHECKING:
    from .. import PixieContext

from urllib.parse import urlparse

from .options import DhcpOptions, build_options, split_query

#: Backends netboot ships, and the extra each needs. Used only to turn "no
#: backend for scheme" into a message that names the fix.
_SHIPPED = {
    "dnsmasq": "netboot[ssh] for remote paths (local needs nothing)",
    "kea": "netboot[kea]",
    "dhcpd": "netboot[dhcpd]",
    "windhcp": "netboot[winrm] for WinRM (ssh needs nothing)",
}

#: Scheme -> the module that defines it, when they differ (`keas://` is the
#: same backend as `kea://`, over https).
_ALIASES = {"keas": "kea"}


def _load_backend(scheme: "str|None") -> None:
    """Import the shipped backend for `scheme`, if there is one.

    Keeps `import netboot.dhcp` free of requests/paramiko/pywinrm: a backend and
    its dependency are only imported when a config actually names its scheme. A
    missing *backend module* is not an error (a plugin may provide the scheme),
    but a backend that fails to import for its own reasons must say so.
    """
    if not scheme or not scheme.isidentifier():
        return
    module = _ALIASES.get(scheme, scheme)
    try:
        _importlib.import_module(f"{__name__}.{module}")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}.{module}":
            raise  # the backend imported, one of *its* imports is missing


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

    #: Query keys this backend reads as connection settings. Everything else in
    #: the query is a client option -- the two sets must stay disjoint.
    SETTINGS: "frozenset[str]" = frozenset()

    def __new__(cls, uri: str):
        if cls is DhcpServer:
            parsed = urlparse(uri)
            scheme = parsed.scheme or cls.DEFAULT_SCHEME
            _load_backend(scheme)
            handlers = [
                sub for sub in _iter_subclasses(cls) if sub.__name__.lower() == scheme
            ]
            if len(handlers) > 1:
                # Two plugins claiming one scheme is a configuration problem,
                # and picking one silently makes it look like the other plugin
                # is broken.
                LOGGER.warning(
                    "scheme %r is claimed by %s; using %s",
                    scheme,
                    ", ".join(f"{h.__module__}.{h.__qualname__}" for h in handlers),
                    handlers[-1].__module__,
                )
            if handlers:
                return object.__new__(handlers[-1])
            hint = _SHIPPED.get(scheme)
            raise ValueError(
                f"No DhcpServer backend registered for scheme {scheme!r} "
                f"(uri={uri!r}); "
                + (
                    f"install {hint}"
                    if hint
                    else "import a plugin module providing it via --load-module"
                )
            )
        return object.__new__(cls)

    def __init__(self, uri: str):
        self.uri = uri
        self.settings, self.options, self.options_builder = split_query(
            uri, self.SETTINGS, type(self).__name__
        )

    def options_for(self, ctx) -> DhcpOptions:
        """The client options for this target on this server, fully merged."""
        return build_options(ctx, self)

    def remove_target(self, netboot: "PixieContext"):
        """Disarm this backend for the target in `netboot` (a `PixieContext`)."""
        pass

    def add_target(self, netboot: "PixieContext"):
        """Arm this backend for the target in `netboot` (a `PixieContext`)."""
        pass


class DhcpZone(Namespace, OpaqueMerge):
    """A network netboot can provision into, as configured under `dhcpzones:`.

    Derives `network` from a CIDR `gateway`, coerces `nameservers`/`search` to
    lists, and builds `dhcpservers` URIs into backends by scheme.
    """

    network: IPNetwork
    gateway: "_ty.Optional[IPAddress]"
    domain: "_ty.Optional[str]"
    search: "list[str]"
    nameservers: "list[IPAddress]"
    globals: dict
    dhcpservers: "list[DhcpServer]"

    @property
    def nameserver(self):
        """The first nameserver, or `""` when none is configured."""
        return self.nameservers[0] if self.nameservers else ""

    def get_local_server(self, servers: "list[IPAddress]", default: IPAddress):
        """The first server that lives inside this zone's network, else `default`.

        Accepts the strings config and globals actually hold, not just parsed
        addresses: `"10.0.0.5" in network` would otherwise raise.
        """
        if isinstance(servers, (str, bytes)) or not isinstance(servers, (list, tuple)):
            servers = [servers]
        for server in servers:
            address = netutils.try_parse(server, IPAddress)
            if address is None:
                LOGGER.warning("ignoring unparseable server address %r", server)
                continue
            if self.network and address in self.network:
                return address
        return default

    def __init__(self, **kwargs) -> None:
        _gateway = kwargs.get("gateway")
        _network = kwargs.get("network")
        _netmask = kwargs.get("netmask")
        if _gateway and not _network:
            gw: netutils.IPv4Interface = netutils.parse(_gateway, IPInterface)
            if not _netmask and isinstance(_gateway, str) and "/" in _gateway:
                _netmask = gw.netmask.exploded
                kwargs["gateway"] = gw.ip.exploded
            if _netmask:
                _network = gw.network.network_address.exploded

        if isinstance(_network, str) and "/" in _network:
            net = netutils.parse(_network, IPNetwork)
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
        self.network = netutils.try_parse(self.network, IPNetwork)
        for prop, ctr in [
            ("nameservers", lambda v: netutils.try_parse(v, IPAddress)),
            ("search", str),
        ]:
            value = getattr(self, prop, None)
            if not value:
                setattr(self, prop, [])
                continue
            # Copy rather than normalise in place: the list may be the caller's
            # config object, shared with other zones through a merge.
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            converted = []
            for val in values:
                new = ctr(val)
                if new is None:
                    # try_parse says "not an address"; dropping it silently
                    # would hand DHCP clients a zone with fewer nameservers
                    # than the operator wrote.
                    LOGGER.warning(
                        "zone %s: ignoring %s entry %r, which is not an address",
                        kwargs.get("_id", "<unnamed>"),
                        prop,
                        val,
                    )
                    continue
                converted.append(new)
            setattr(self, prop, converted)

        for prop in ["gateway", "domain"]:
            if not getattr(self, prop, None):
                setattr(self, prop, None)

        if self.gateway:
            self.gateway = netutils.try_parse(self.gateway, IPAddress)
