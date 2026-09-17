"""DHCP options: one vocabulary netboot speaks, each backend translates.

A backend is configured by a URI. Its **query string** carries two kinds of key,
and they must never be confused:

* a **connection setting** the backend declares in its own ``SETTINGS``
  (where the server is, how to reach it, which files to write);
* everything else is a **client option** -- what the DHCP server should tell the
  machine.

A third kind exists and is deliberately *not* accepted there: anything that
varies per target or per zone (:data:`APPLY_TIME`). A boot file pinned to a
connection would hand every target on that server the same one, invisibly, so it
is rejected with a message naming where it belongs.
"""

from __future__ import annotations

import typing as _ty
from urllib.parse import parse_qsl, urlsplit

from ..logging import LOGGER
from ..utils.misc import import_

#: Option names netboot understands and every backend can translate. A numeric
#: `option-<n>` (or a bare number) is always accepted too, for anything not
#: modelled here.
GENERIC_OPTIONS = frozenset(
    {
        "router",
        "domain-name-servers",
        "domain-name",
        "domain-search",
        "next-server",
        "boot-file-name",
        "tftp-server-name",
        "host-name",
        "subnet-mask",
        "broadcast-address",
        "lease-time",
        "ntp-servers",
        "vendor-class-identifier",
    }
)

#: Resolved when a target is applied, never from a connection string: these
#: depend on which machine is being provisioned, or on its zone. The value is
#: where the operator should put it instead.
APPLY_TIME = {
    "subnet_id": "the zone (dhcpzones.<id>.subnet_id)",
    "scope": "the zone (dhcpzones.<id>.scope)",
    "boot-file-name": "the image or target (dhcp_options)",
    "next-server": "the image or target (dhcp_options)",
    "tftp-server-name": "the image or target (dhcp_options)",
    "host-name": "the target itself (it is the target's hostname)",
}

#: Query key naming an importable ``fn(ctx, options) -> options``.
OPTIONS_BUILDER = "options_builder"

#: Query-key prefix for backend-native text netboot never translates:
#: ``raw.dnsmasq=dhcp-option=tag:x,66,10.0.0.2``.
RAW_PREFIX = "raw."


def is_option(name: str) -> bool:
    """Is `name` a client option netboot can carry to some backend?"""
    if name in GENERIC_OPTIONS:
        return True
    if name.startswith("option-"):
        return name[len("option-") :].isdigit()
    return name.isdigit()


class DhcpOptions(dict):
    """Client options for one target, plus untranslated per-backend text.

    A plain ``dict`` of generic name -> value, so a backend translates it by
    iterating; `raw` holds backend-native strings keyed by backend name.
    """

    def __init__(self, *args, raw: "dict[str, list[str]]|None" = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw: "dict[str, list[str]]" = {k: list(v) for k, v in (raw or {}).items()}

    def raw_for(self, backend: str) -> "list[str]":
        """The untranslated lines this backend should emit verbatim."""
        return list(self.raw.get(backend, ()))

    def copy(self) -> "DhcpOptions":
        return DhcpOptions(self, raw=self.raw)


def split_query(uri: str, settings: "_ty.Iterable[str]", backend: str):
    """Split a backend URI's query into (settings, options, builder).

    `settings` is the backend's own ``SETTINGS``. Every other key is a client
    option, `raw.<backend>`, or `options_builder`. An unknown key raises, rather
    than being dropped where nobody would see it.
    """
    from .. import PixieConfigError  # circular at module import; fine here

    known = set(settings)
    found_settings: "dict[str, str]" = {}
    options = DhcpOptions()
    builder = None

    for key, value in parse_qsl(urlsplit(uri).query, keep_blank_values=True):
        if key in known:
            found_settings[key] = value
        elif key == OPTIONS_BUILDER:
            builder = value
        elif key.startswith(RAW_PREFIX):
            options.raw.setdefault(key[len(RAW_PREFIX) :], []).append(value)
        elif key in APPLY_TIME:
            raise PixieConfigError(
                f"{key!r} cannot be set on a {backend} connection: it depends on "
                f"the target being provisioned. Put it in {APPLY_TIME[key]}."
            )
        elif is_option(key):
            _add(options, key, value)
        else:
            raise PixieConfigError(
                f"unknown key {key!r} in a {backend} connection: it is neither a "
                f"{backend} setting ({', '.join(sorted(known)) or 'none'}) nor a "
                f"DHCP option ({', '.join(sorted(GENERIC_OPTIONS))}, option-<n>)"
            )
    return found_settings, options, builder


def _add(options: DhcpOptions, key: str, value) -> None:
    """Set or extend `key`; a repeated key is how a multi-valued option is written."""
    if key not in options:
        options[key] = value
        return
    current = options[key]
    if not isinstance(current, list):
        current = [current]
    current.append(value)
    options[key] = current


def zone_defaults(zone) -> DhcpOptions:
    """What the zone already knows, before anyone configures an option."""
    options = DhcpOptions()
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


def build_options(ctx, server) -> DhcpOptions:
    """The options for this target on this server, in merge order.

    Later wins: zone defaults, the server's connection query, the image's
    `dhcp_options`, the target's `dhcp_options`, the server's `options_builder`,
    then the `BuildDhcpOptions` hook. The last two may return anything mapping-
    like; both must return the options they were given.
    """
    from .. import PixieConfigError, PixieEvent

    options = zone_defaults(ctx.dhcpzone)
    options.update(server.options)
    options.raw.update(server.options.raw)

    for source in (ctx.image, ctx.target):
        declared = getattr(source, "dhcp_options", None) or {}
        if not isinstance(declared, dict):
            raise PixieConfigError(
                f"dhcp_options on {getattr(source, '_id', source)!r} must be a "
                f"mapping, got {type(declared).__name__}"
            )
        for key, value in declared.items():
            if not is_option(key):
                raise PixieConfigError(
                    f"unknown dhcp option {key!r} on "
                    f"{getattr(source, '_id', source)!r}"
                )
            options[key] = value

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
    return options


def _call_builder(builder, ctx, options: DhcpOptions) -> DhcpOptions:
    """Resolve and call an `options_builder`, insisting it returns the options."""
    from .. import PixieConfigError

    fn = builder if callable(builder) else import_(builder)
    result = fn(ctx, options)
    if not isinstance(result, dict):
        raise PixieConfigError(
            f"options_builder {builder!r} must return the options mapping, got "
            f"{type(result).__name__}"
        )
    if not isinstance(result, DhcpOptions):
        # A builder may return a plain dict; keep the raw section it cannot see.
        merged = options.copy()
        merged.clear()
        merged.update(result)
        result = merged
    LOGGER.debug("options_builder %r produced %d option(s)", builder, len(result))
    return result
