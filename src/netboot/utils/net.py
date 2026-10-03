"""Network helpers for netboot, backed by :mod:`netimps`.

The IP/MAC machinery used to live in an in-tree copy of an earlier version of
that library. It has since been published, gaining fixes this copy never had --
real prefix lengths and MTU in interface enumeration, DNS errors that surface
instead of being swallowed, and a ``ping`` that speaks each platform's own
flags -- so the copy is gone.

This module re-exports netimps under netboot's own name rather than wrapping it:
the point of adopting a library is to use its vocabulary. :class:`Host` is the
one subclass, and it adds no behaviour -- only the two names netboot's own
``Host`` published before netimps' was adopted.
"""

from __future__ import annotations

from netimps import Host as _NetimpsHost
from netimps import (  # noqa: F401
    IPAddress,
    IPInterface,
    IPNetwork,
    IPv4Address,
    IPv4Interface,
    MACAddress,
    get_interfaces,
    is_valid,
    is_wildcard,
    iter_addresses,
    normalize_host,
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
    "is_wildcard",
    "iter_addresses",
    "normalize_host",
    "parse",
    "ping",
    "resolve",
    "try_parse",
]


class Host(_NetimpsHost):
    """netimps' :class:`~netimps.Host`, keeping the two names netboot published.

    The implementation is netimps': a hostname-or-IP address whose ``.ip()``
    resolves once and **caches the result, failures included** (``refresh=True``
    retries), with ``.is_address`` answering without DNS and ``.value`` holding
    the configured text. netboot's own class duplicated that type on the claim
    that a host was a netboot concept; it was not.

    What survives here is the vocabulary that duplicate published, because
    dropping it would break a documented API for no gain:

    * ``.try_ip()`` -- ``.ip()`` with the original text as the fallback, which is
      what every netboot caller wants when it has a URL to build either way.
      netimps deliberately keeps ``.ip()`` optional instead, so the type stays
      honest; this is that one ``or``, spelled once.
    * ``.address`` -- the old name for ``.value``.
    * ``Host()`` with no argument, which netimps requires.

    New code can use either; the netimps names are the ones that will still be
    here if this subclass ever goes away.
    """

    def __init__(self, address: "str | _NetimpsHost | None" = None) -> None:
        super().__init__(address)

    @property
    def address(self) -> str:
        """The configured text. netimps calls this ``.value``."""
        return self.value

    @address.setter
    def address(self, value: "str | _NetimpsHost | None") -> None:
        # Re-running __init__ rather than assigning `.value`: a new address must
        # drop the cached resolution, or `.ip()` answers for the old one.
        self.__init__(value)

    def try_ip(self) -> "IPAddress | str":
        """Resolve to an address, falling back to the text as configured.

        Cached by netimps, so repeated calls on one host cost one lookup -- the
        duplicate this replaced re-resolved every time.
        """
        return self.ip() or str(self)
