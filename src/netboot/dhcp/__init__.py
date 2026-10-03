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

from .conditions import (
    ConditionMissing,
    ConditionUnsupported,
    DhcpCondition,
    build_conditions,
)
from .options import PHASES, DhcpOptions, build_options, split_query

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

    def conditions_for(self, ctx) -> "dict[str, DhcpCondition]":
        """The named conditions that apply to this target, layered by name."""
        return build_conditions(ctx)

    def ensure_condition(self, ctx, condition: DhcpCondition) -> str:
        """The name of the construct serving `condition`, creating it if needed.

        The ladder every backend walks, in this order:

        1. **it exists** (by name) -- use it, and attempt nothing else. This is
           the normal state after the first target is armed, and it is also what
           makes netboot usable on a server it has no rights to manage.
        2. **it is absent and this backend can create it** -- create, then use.
        3. **it is absent and cannot be created** -- raise `ConditionMissing`
           carrying `condition_recipe()`, so the error *is* the fix.
        4. **this backend cannot express a condition at all** -- raise
           `ConditionUnsupported`, which is this base implementation.

        Arming is best effort per server, so either exception leaves the other
        servers in the zone armed and fails the run only if none succeeded.
        """
        raise ConditionUnsupported(
            f"{type(self).__name__} cannot serve conditional options, so "
            f"dhcp_when.{condition.name} cannot be applied on {self.uri}"
        )

    def condition_recipe(self, ctx, condition: DhcpCondition) -> str:
        """What a privileged operator must apply, in this server's own syntax.

        Returned as text, not run. It is the body of a `ConditionMissing` error
        **and** what `pixie dhcp-config` prints, deliberately the same string so
        the two can never drift.
        """
        return ""

    def remove_condition_member(self, ctx, condition: DhcpCondition) -> None:
        """Detach this target from `condition`, leaving the construct alone.

        Called when a completed target **keeps** its reservation: the construct is
        shared by every target that names it, so removing it would break them.
        A backend whose construct has no member list (a Windows policy matches a
        condition, it does not hold members) has nothing to do here.
        """

    def extras(self, ctx, phase: str = "add") -> "list":
        """Backend-native fragments to apply along with the reservation.

        This is the extension point for anything netboot does not model: an
        extra statement, an extra command, a conditional that makes the server
        answer two kinds of client differently (the iPXE chainload is the usual
        one -- hand a PXE ROM the iPXE binary, then hand iPXE the script).

        Two ways in, and they end up in the same list:

        - **from config**, `raw.<backend>=...` on the connection, or
          `raw.<backend>.remove=...` for teardown;
        - **from code**, by overriding this method in a `DhcpServer` subclass,
          which is the one that can look at `ctx` and decide per target:

          ```python
          class dhcpd(netboot.dhcp.dhcpd.dhcpd):
              def extras(self, ctx, phase="add"):
                  lines = super().extras(ctx, phase)
                  if phase == "add" and ctx.image._id.startswith("ipxe"):
                      lines.append('if exists user-class ... { filename "boot.ipxe"; }')
                  return lines
          ```

        What a fragment *is* belongs to the backend and is documented there:
        dhcpd takes config statements, dnsmasq config lines, kea `option-data`
        entries, windhcp PowerShell lines (or, under `method=netsh`, a
        `{"Args": [...], "Ignore": bool}` command). A backend calls this once
        per phase and emits whatever comes back **verbatim** -- netboot does not
        parse, validate or escape it, which is the point of an escape hatch and
        also the risk of one.
        """
        if phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}, not {phase!r}")
        options = getattr(self, "options", None)
        if options is None:  # pragma: no cover - a subclass that skips __init__
            return []
        return options.raw_for(type(self).__name__, phase)

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
