"""Network helpers for netboot, backed by :mod:`netimps`.

The IP/MAC machinery used to live in an in-tree copy of an earlier version of
that library. It has since been published, gaining fixes this copy never had --
real prefix lengths and MTU in interface enumeration, DNS errors that surface
instead of being swallowed, and a ``ping`` that behaves the same on every
platform -- so the copy is gone.

This module re-exports netimps under netboot's own name rather than wrapping
it: the point of adopting a library is to use its vocabulary. Only
:class:`Host` is netboot's own, because it is a netboot concept.
"""

from __future__ import annotations

from netimps import (  # noqa: F401
    IPAddress,
    IPInterface,
    IPNetwork,
    IPv4Address,
    IPv4Interface,
    MACAddress,
    get_interfaces,
    is_valid,
    iter_addresses,
    parse,
    ping,
    resolve,
    try_parse,
)

__all__ = [
    "Host",
    "IPAddress",
    "IPInterface",
    "IPNetwork",
    "IPv4Address",
    "IPv4Interface",
    "MACAddress",
    "get_interfaces",
    "is_valid",
    "iter_addresses",
    "parse",
    "ping",
    "resolve",
    "try_parse",
]


class Host:
    """A repository/service address that may be a hostname or an IP.

    Content repos are addressed by hostname or IP in config; ``try_ip`` resolves
    to an IP address when possible (an IP literal as-is, a hostname via DNS),
    falling back to the original string when resolution fails so URLs can still
    be built.
    """

    def __init__(self, address: "str | Host | None" = None) -> None:
        if isinstance(address, Host):
            address = address.address
        self.address = "" if address is None else str(address)

    def try_ip(self) -> "IPAddress | str":
        """Best-effort resolve to an IP; return the raw address on failure."""
        if not self.address:
            return self.address
        literal = try_parse(self.address, IPAddress)
        if literal is not None:
            return literal
        resolved = resolve(self.address)
        if resolved:
            return resolved[0]
        return self.address

    def __str__(self) -> str:
        return self.address

    def __repr__(self) -> str:
        return f"Host({self.address!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Host):
            return self.address == other.address
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.address)
