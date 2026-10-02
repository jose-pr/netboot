"""Network helpers for netboot, backed by :mod:`netimps`.

The IP/MAC machinery used to live in an in-tree copy of an earlier version of
that library. It has since been published, gaining fixes this copy never had --
real prefix lengths and MTU in interface enumeration, DNS errors that surface
instead of being swallowed, and a ``ping`` that speaks each platform's own
flags -- so the copy is gone.

This module re-exports netimps under netboot's own name rather than wrapping
it: the point of adopting a library is to use its vocabulary. Nothing here is
netboot's own -- :class:`Host` used to be, on the claim that it was a netboot
concept, and it was the same type netimps has had since 0.2.0 with the same
stated justification. Resolve with ``host.ip() or str(host)``: ``.ip()`` is
``Optional`` so the type stays honest, and ``str(host)`` is always the text that
was configured, so the fallback is never lost.
"""

from __future__ import annotations

from netimps import (  # noqa: F401
    Host,
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
