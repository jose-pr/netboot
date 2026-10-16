"""DHCP zones, and what a target's reservation is on each of their servers.

The servers are dhcpctl's: a zone's `dhcpservers` are URIs, built with
`dhcpctl.connect`, and a backend for a new scheme is registered with
`dhcpctl.register_backend` (or the `dhcpctl.backends` entry point). What stays
here is what is about targets: the zone -> image -> target layering of
`dhcp_options`, `dhcp_when` and `dhcp_complete`, a server's `options_builder`,
the `BuildDhcpOptions` hook, and the secrets netboot reads from `PIXIE_*`.
"""

from __future__ import annotations

import contextlib
import os
import typing as _ty
from argparse import Namespace

import dhcpctl
import netimps
from netimps import IPAddress, IPInterface, IPNetwork
from yaconfiglib import OpaqueMerge

from .logging import LOGGER
from .utils.misc import import_

if _ty.TYPE_CHECKING:
    from . import PixieContext

__all__ = [
    "DhcpZone",
    "WINDHCP_PASSWORD_ENV_VAR",
    "WINDHCP_USER_ENV_VAR",
    "DHCPD_OMAPI_KEY_ENV_VAR",
    "OWNER",
    "connect",
    "scope_for",
    "zone_defaults",
    "build_options",
    "build_conditions",
    "build_completion",
    "reservation_for",
    "arm",
    "disarm",
]

#: Where a Windows DHCP account's password is read from. Never the URI.
WINDHCP_PASSWORD_ENV_VAR = "PIXIE_WINDHCP_PASSWORD"
#: The Windows DHCP account, when the URI names none.
WINDHCP_USER_ENV_VAR = "PIXIE_WINDHCP_USER"
#: The dhcpd OMAPI secret, when the URI gives no `keyfile=`.
DHCPD_OMAPI_KEY_ENV_VAR = "PIXIE_DHCPD_OMAPI_KEY"

#: What marks the entries netboot writes on a server: dnsmasq region markers,
#: Windows policy names, recipe comments.
OWNER = "netboot"

#: Scheme -> dhcpctl secret name -> the variable it is read from.
_SECRETS = {
    "windhcp": {
        "password": WINDHCP_PASSWORD_ENV_VAR,
        "user": WINDHCP_USER_ENV_VAR,
    },
    "dhcpd": {"omapi_key": DHCPD_OMAPI_KEY_ENV_VAR},
}


def connect(uri: str) -> dhcpctl.DHCPServer:
    """The dhcpctl server for `uri`; secrets the URI lacks come from `PIXIE_*`.

    :raises PixieConfigError: the URI cannot be used.
    """
    from . import PixieConfigError

    names = _SECRETS.get(uri.partition(":")[0].lower(), {})

    def secret(key: str) -> "str|None":
        variable = names.get(key)
        return os.environ.get(variable) if variable else None

    try:
        return dhcpctl.connect(uri, secrets=secret, owner=OWNER)
    except dhcpctl.ConfigError as exc:
        raise PixieConfigError(str(exc)) from exc


class DhcpZone(Namespace, OpaqueMerge):
    """A network netboot can provision into, as configured under `dhcpzones:`.

    Derives `network` from a CIDR `gateway`, coerces `nameservers`/`search` to
    lists, and builds `dhcpservers` URIs into dhcpctl servers.
    """

    network: IPNetwork
    gateway: "_ty.Optional[IPAddress]"
    domain: "_ty.Optional[str]"
    search: "list[str]"
    nameservers: "list[IPAddress]"
    globals: dict
    dhcpservers: "list[dhcpctl.DHCPServer]"

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
            address = netimps.try_parse(server, IPAddress)
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
            gw: netimps.IPv4Interface = netimps.parse(_gateway, IPInterface)
            if not _netmask and isinstance(_gateway, str) and "/" in _gateway:
                _netmask = gw.netmask.exploded
                kwargs["gateway"] = gw.ip.exploded
            if _netmask:
                _network = gw.network.network_address.exploded

        if isinstance(_network, str) and "/" in _network:
            net = netimps.parse(_network, IPNetwork)
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
            server if isinstance(server, dhcpctl.DHCPServer) else connect(server)
            for server in (self.dhcpservers or [])
        ]
        self.network = netimps.try_parse(self.network, IPNetwork)
        for prop, ctr in [
            ("nameservers", lambda v: netimps.try_parse(v, IPAddress)),
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
            self.gateway = netimps.try_parse(self.gateway, IPAddress)


def scope_for(zone) -> dhcpctl.Scope:
    """A zone as the scope a reservation lives in: `subnet_id`/`scope` if set."""
    subnet_id = getattr(zone, "subnet_id", None)
    return dhcpctl.Scope(
        name=str(getattr(zone, "_id", "") or ""),
        network=getattr(zone, "network", None),
        subnet_id=int(subnet_id) if subnet_id is not None else None,
        scope_id=str(getattr(zone, "scope", "") or "") or None,
    )


# -- options -----------------------------------------------------------------


def zone_defaults(zone) -> dhcpctl.DHCPOptions:
    """What the zone already knows, before anyone configures an option."""
    options = dhcpctl.DHCPOptions()
    if getattr(zone, "gateway", None):
        options["router"] = zone.gateway
    if getattr(zone, "nameservers", None):
        options["domain-name-servers"] = list(zone.nameservers)
    if getattr(zone, "domain", None):
        options["domain-name"] = zone.domain
    if getattr(zone, "search", None):
        options["domain-search"] = list(zone.search)
    network = getattr(zone, "network", None)
    if network is not None and getattr(network, "netmask", None) is not None:
        options["subnet-mask"] = network.netmask
        broadcast = getattr(network, "broadcast_address", None)
        if broadcast is not None:
            options["broadcast-address"] = broadcast
    return options


def build_options(ctx, server: dhcpctl.DHCPServer) -> dhcpctl.DHCPOptions:
    """The options for this target on this server, in merge order.

    Later wins: zone defaults, the server's connection query, the image's
    `dhcp_options`, the target's `dhcp_options`, the server's `options_builder`,
    then the `BuildDhcpOptions` hook. The last two may return anything mapping-
    like; both must return the options they were given.
    """
    from . import PixieConfigError, PixieEvent

    layers = []
    for source in (ctx.image, ctx.target):
        declared = getattr(source, "dhcp_options", None) or {}
        if not isinstance(declared, dict):
            raise PixieConfigError(
                f"dhcp_options on {getattr(source, '_id', source)!r} must be a "
                f"mapping, got {type(declared).__name__}"
            )
        for key in declared:
            if not dhcpctl.is_option(key):
                raise PixieConfigError(
                    f"unknown dhcp option {key!r} on "
                    f"{getattr(source, '_id', source)!r}"
                )
        layers.append(declared)
    options = server.client_options(*layers, defaults=zone_defaults(ctx.dhcpzone))

    if server.options_builder:
        options = _call_builder(server.options_builder, ctx, options)

    netboot = getattr(ctx, "_netboot_", None)
    if netboot is not None:
        options = netboot.hook(
            PixieEvent.BuildDhcpOptions, options, target=ctx.target, server=server
        )
    if not isinstance(options, dict):
        raise PixieConfigError(
            "a BuildDhcpOptions hook must return the options mapping, got "
            f"{type(options).__name__}"
        )
    if not isinstance(options, dhcpctl.DHCPOptions):
        options = dhcpctl.DHCPOptions(options)
    return options


def _call_builder(builder, ctx, options: dhcpctl.DHCPOptions) -> dhcpctl.DHCPOptions:
    """Resolve and call an `options_builder`, insisting it returns the options."""
    from . import PixieConfigError

    fn = builder if callable(builder) else import_(builder)
    result = fn(ctx, options)
    if not isinstance(result, dict):
        raise PixieConfigError(
            f"options_builder {builder!r} must return the options mapping, got "
            f"{type(result).__name__}"
        )
    if not isinstance(result, dhcpctl.DHCPOptions):
        # A builder may return a plain dict; keep the raw section it cannot see.
        merged = options.copy()
        merged.clear()
        merged.update(result)
        result = merged
    LOGGER.debug("options_builder %r produced %d option(s)", builder, len(result))
    return result


# -- conditions and completion -------------------------------------------------


def build_conditions(ctx) -> "dict[str, dhcpctl.Condition]":
    """Every `dhcp_when` condition that applies to this target, layered by name.

    Zone, then image, then target -- later wins, **by name**, so a target
    redeclaring `ipxe` replaces the image's rather than adding a second one.
    """
    conditions: "dict[str, dhcpctl.Condition]" = {}
    for source in (ctx.dhcpzone, ctx.image, ctx.target):
        if source is None:
            continue
        conditions.update(
            _config(
                dhcpctl.parse_conditions,
                getattr(source, "dhcp_when", None),
                getattr(source, "_id", source),
            )
        )
    return conditions


def build_completion(ctx) -> dhcpctl.Completion:
    """How this target completes: zone, then image, then target -- later wins."""
    completion = dhcpctl.Completion()
    for source in (ctx.dhcpzone, ctx.image, ctx.target):
        if source is None:
            continue
        declared = _config(
            dhcpctl.parse_completion,
            getattr(source, "dhcp_complete", None),
            getattr(source, "_id", source),
        )
        if declared is not None:
            completion = declared
    return completion


def _config(parse, declared, where):
    """Run a dhcpctl parser, reporting a bad value as netboot's config error."""
    from . import PixieConfigError

    try:
        return parse(declared, where)
    except dhcpctl.ConfigError as exc:
        raise PixieConfigError(str(exc)) from exc


# -- the reservation -----------------------------------------------------------


def reservation_for(
    ctx: "PixieContext", server: dhcpctl.DHCPServer
) -> dhcpctl.Reservation:
    """This target's reservation on `server`, with `ctx` as its context."""
    target = ctx.target
    mac = target.mac
    if mac is not None and str(mac) == dhcpctl.NULL_MAC:
        mac = None
    return dhcpctl.Reservation(
        target=dhcpctl.Target(
            name=str(target._id),
            mac=mac,
            ip=target.ip or None,
            hostname=str(getattr(target, "hostname", "") or ""),
        ),
        scope=scope_for(ctx.dhcpzone),
        options=build_options(ctx, server),
        conditions=build_conditions(ctx),
        completion=build_completion(ctx),
        context=ctx,
    )


def arm(ctx: "PixieContext", server: dhcpctl.DHCPServer) -> None:
    """Arm `server` for this target."""
    with _lookup_errors():
        server.add(reservation_for(ctx, server))


def disarm(ctx: "PixieContext", server: dhcpctl.DHCPServer) -> None:
    """Disarm `server` for this target, honouring `dhcp_complete:`."""
    with _lookup_errors():
        server.complete(reservation_for(ctx, server))


@contextlib.contextmanager
def _lookup_errors():
    """Report a target the server cannot identify as netboot's lookup error."""
    from . import PixieLookupError

    try:
        yield
    except dhcpctl.MissingMACError as exc:
        raise PixieLookupError(str(exc)) from exc
