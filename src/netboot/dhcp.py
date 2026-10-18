"""DHCP zones, and what a target's reservation is on each of their servers.

The servers are dhcpctl's: a zone's `dhcpservers` are URIs, built with
`dhcpctl.connect`, and a backend for a new scheme is registered with
`dhcpctl.register_backend` (or the `dhcpctl.backends` entry point). What stays
here is what is about targets: the zone -> image -> target layering of
`dhcp_options`, `dhcp_when` and `dhcp_complete`, the `BuildDhcpOptions` hook,
and each server's own secrets from its `dhcpservers` entry.
"""

from __future__ import annotations

import collections.abc as _abc
import contextlib
import typing as _ty
from argparse import Namespace

import dhcpctl
import netimps
from netimps import IPAddress, IPInterface, IPNetwork
from yaconfiglib import OpaqueMerge

from .exceptions import PixieConfigError, PixieLookupError
from .logging import LOGGER

if _ty.TYPE_CHECKING:
    from . import PixieContext

__all__ = [
    "DhcpZone",
    "OWNER",
    "LEGACY_OPTION_NAMES",
    "NETBOOT_CODES",
    "codes_for",
    "connect",
    "server_from",
    "scope_for",
    "zone_defaults",
    "build_options",
    "build_lease",
    "build_conditions",
    "build_completion",
    "reservation_for",
    "arm",
    "disarm",
]

#: What marks the entries netboot writes on a server: dnsmasq region markers,
#: Windows policy names, recipe comments.
OWNER = "netboot"

#: Names netboot configs have always used, as aliases of pydhcp's options and
#: of the lease's `file` header field.
LEGACY_OPTION_NAMES = {
    "domain-name-servers": "dns",
    "boot-file-name": "file",
    "tftp-server-name": "tftp-server",
    "host-name": "hostname",
    "lease-time": "ip-address-lease-time",
}

#: The codemap a config with no `dhcp.options` reads names with: pydhcp's
#: names and netboot's own.
NETBOOT_CODES = dhcpctl.option_codes(LEGACY_OPTION_NAMES)


def codes_for(dhcp: "dict|None" = None) -> "type[dhcpctl.OptionCodes]":
    """The codemap a config's `dhcp:` section declares, over netboot's.

    `dhcp.options` maps a name to `{code: <n>, type: <pydhcp codec>}` or to a
    code or another name (see `dhcpctl.option_codes`).

    :raises PixieConfigError: the section cannot be used.
    """

    dhcp = dhcp or {}
    if not isinstance(dhcp, dict) or not isinstance(dhcp.get("options") or {}, dict):
        raise PixieConfigError("dhcp.options must be a mapping of option names")
    declared = dhcp.get("options")
    if not declared:
        return NETBOOT_CODES
    try:
        return dhcpctl.option_codes(declared, base=NETBOOT_CODES)
    except dhcpctl.ConfigError as exc:
        raise PixieConfigError(f"dhcp.{exc}") from exc


def connect(
    uri: str,
    codes: "type[dhcpctl.OptionCodes]|None" = None,
    secrets: "_abc.Mapping[str, str]|None" = None,
) -> dhcpctl.DHCPServer:
    """The dhcpctl server for `uri`, with the `secrets` its config entry gives.

    A secret given here overrides the one the URI carries (its user or
    password). Option names are read with `codes` (netboot's by default).

    :raises PixieConfigError: the URI cannot be used.
    """

    try:
        return dhcpctl.connect(
            uri, secrets=dict(secrets or {}), owner=OWNER, codes=codes or NETBOOT_CODES
        )
    except dhcpctl.ConfigError as exc:
        raise PixieConfigError(str(exc)) from exc


def server_from(entry, codes=None) -> dhcpctl.DHCPServer:
    """A `dhcpservers` entry as a server: a URI, or a mapping of `uri:` and the
    secrets that backend takes (windhcp and kea `user`/`password`, dhcpd
    `keyname`/`omapi_key`), each written in the config or produced by it
    (`password: "{{ env.WINDHCP_PASSWORD }}"`).

    :raises PixieConfigError: the entry cannot be used.
    """

    if isinstance(entry, dhcpctl.DHCPServer):
        return entry
    if isinstance(entry, str):
        return connect(entry, codes)
    if not isinstance(entry, _abc.Mapping) or not isinstance(entry.get("uri"), str):
        raise PixieConfigError(
            "a dhcpservers entry is a URI, or a mapping with `uri:` and that "
            f"server's secrets, not {type(entry).__name__}"
        )
    uri = entry["uri"]
    secrets = {key: value for key, value in entry.items() if key != "uri"}
    try:
        backend = dhcpctl.get_backend(uri.partition(":")[0])
    except dhcpctl.ConfigError as exc:
        raise PixieConfigError(str(exc)) from exc
    accepted = sorted(set(backend.URI_SECRETS.values()))
    unknown = sorted(set(map(str, secrets)) - set(accepted))
    if unknown:
        raise PixieConfigError(
            f"dhcpservers entry {uri.partition('?')[0]!r}: unknown key(s) "
            f"{', '.join(unknown)}; it takes uri and {', '.join(accepted)}"
        )
    return connect(
        uri,
        codes,
        {key: str(value) for key, value in secrets.items() if value is not None},
    )


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
        codes = kwargs.pop("codes", None) or NETBOOT_CODES
        kwargs.setdefault("dhcpservers", [])
        super().__init__(**kwargs)
        self.dhcpservers = [
            server_from(server, codes) for server in (self.dhcpservers or [])
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


def zone_defaults(zone, codes=None) -> dhcpctl.DHCPOptions:
    """What the zone already knows, before anyone configures an option.

    The bag reads its options with `codes` (netboot's by default), the codemap
    every layer merged onto it keeps.
    """
    known = {}
    if getattr(zone, "gateway", None):
        known["router"] = zone.gateway
    if getattr(zone, "nameservers", None):
        known["dns"] = list(zone.nameservers)
    if getattr(zone, "domain", None):
        known["domain-name"] = zone.domain
    if getattr(zone, "search", None):
        known["domain-search"] = list(zone.search)
    network = getattr(zone, "network", None)
    if network is not None and getattr(network, "netmask", None) is not None:
        known["subnet-mask"] = network.netmask
        broadcast = getattr(network, "broadcast_address", None)
        if broadcast is not None:
            known["broadcast-address"] = broadcast
    return (codes or NETBOOT_CODES).options(
        known, str(getattr(zone, "_id", "") or "zone")
    )


def _declared(ctx) -> "list[dhcpctl.DHCPLease]":
    """The image's and the target's `dhcp_options`, each as what it serves.

    `next-server` and `boot-file-name` there are the BOOTP header fields
    `siaddr` and `file`, not options: each lease carries them beside its
    options.
    """

    layers = []
    for source in (ctx.image, ctx.target):
        where = getattr(source, "_id", source)
        declared = getattr(source, "dhcp_options", None) or {}
        if not isinstance(declared, dict):
            raise PixieConfigError(
                f"dhcp_options on {where!r} must be a mapping, got "
                f"{type(declared).__name__}"
            )
        try:
            layers.append(_codes(ctx).lease(declared))
        except dhcpctl.ConfigError as exc:
            raise PixieConfigError(f"dhcp_options on {where!r}: {exc}") from exc
    return layers


def build_options(ctx, server: dhcpctl.DHCPServer) -> dhcpctl.DHCPOptions:
    """The options for this target on this server, in merge order.

    Later wins: zone defaults, the image's `dhcp_options`, the target's
    `dhcp_options`, then the `BuildDhcpOptions` hook, which receives pydhcp
    `DHCPOptions` and returns them, or any mapping of option names.
    """
    from . import PixieEvent

    layers = [lease.options for lease in _declared(ctx)]
    options = zone_defaults(ctx.dhcpzone, _codes(ctx)).copy(*layers)

    netboot = getattr(ctx, "_netboot_", None)
    if netboot is not None:
        options = _returned(
            "a BuildDhcpOptions hook",
            netboot.hook(
                PixieEvent.BuildDhcpOptions, options, target=ctx.target, server=server
            ),
            _codes(ctx),
        )
    return options


def build_lease(ctx, server: dhcpctl.DHCPServer) -> dhcpctl.DHCPLease:
    """What this target is served on this server: its address, `build_options`,
    and the header fields the image or the target sets, the target's first."""

    fields = {}
    for lease in _declared(ctx):
        if not lease.siaddr.is_unspecified:
            fields["siaddr"] = lease.siaddr
        for name in ("sname", "file"):
            if getattr(lease, name):
                fields[name] = getattr(lease, name)
    try:
        return dhcpctl.DHCPLease(
            ctx.target.ip or dhcpctl.UNSPECIFIED,
            options=build_options(ctx, server),
            strict=False,
            **fields,
        )
    except ValueError as exc:
        raise PixieConfigError(f"target {ctx.target._id!r}: {exc}") from exc


def _returned(who: str, result, codes) -> dhcpctl.DHCPOptions:
    """What a hook returned, as options, or the reason it cannot be."""

    if not isinstance(result, _abc.Mapping):
        raise PixieConfigError(
            f"{who} must return the options mapping, got {type(result).__name__}"
        )
    try:
        return codes.options(result)
    except dhcpctl.ConfigError as exc:
        raise PixieConfigError(f"{who}: {exc}") from exc


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
                _codes(ctx),
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
            _codes(ctx),
        )
        if declared is not None:
            completion = declared
    return completion


def _codes(ctx) -> "type[dhcpctl.OptionCodes]":
    """The codemap this context's engine reads its config with."""
    return getattr(getattr(ctx, "_netboot_", None), "option_codes", NETBOOT_CODES)


def _config(parse, declared, where, names):
    """Run a dhcpctl parser, reporting a bad value as netboot's config error."""

    try:
        return parse(declared, where, names)
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
            hostname=str(getattr(target, "hostname", "") or ""),
        ),
        scope=scope_for(ctx.dhcpzone),
        lease=build_lease(ctx, server),
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

    try:
        yield
    except dhcpctl.MissingMACError as exc:
        raise PixieLookupError(str(exc)) from exc
