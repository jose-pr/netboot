"""Network helpers for piskie.

The IP/MAC address machinery lives in the vendored :mod:`piskie._netutils` module;
re-export the names piskie uses so the rest of the package can import them from a
single place (``piskie.utils``).
"""

from .._netutils import (  # noqa: F401
    IPAddress,
    IPInterface,
    IPNetwork,
    IPv4Address,
    IPv4Interface,
    MACAddress,
    active_nic_addresses,
    is_valid_ip,
    nslookup,
    parse_ip,
    parse_network,
    ping,
)


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
        if is_valid_ip(self.address):
            return parse_ip(self.address)
        resolved = nslookup(self.address)
        if resolved:
            return parse_ip(resolved[0])
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
