"""The config objects a target and an image are built into."""

from argparse import Namespace
from pathlib import Path
from typing import Union

import netimps
from anycfg import OpaqueMerge
from netimps import IPAddress, MACAddress

from .content import Resource
from .exceptions import PixieConfigError
from .logging import LOGGER

__all__ = ["PixieImage", "PixieTarget"]


class PixieTarget(Namespace, OpaqueMerge):
    """One machine to provision, as configured under `targets:`.

    Gaps are filled at construction: a MAC-shaped id becomes the MAC, an
    IP-shaped id the IP, anything else the hostname. An unknown IP is looked
    up only by `resolve_ip`, which the engine calls for a target it acts on,
    so building the engine does no DNS.
    """

    _id: str
    hostname: str
    #: An `IPAddress` once known, but `""` while it is not: a MAC-keyed target
    #: whose hostname did not resolve has no address, and callers test it for
    #: truthiness rather than assuming one.
    ip: "Union[IPAddress, str]"
    mac: MACAddress
    image: str
    dhcpzone: str
    globals: dict
    template_path: list[Union[str, Path]]

    #: MAC value treated as "unset" (a target keyed by hostname/IP has no MAC).
    _NULL_MAC = "00:00:00:00:00:00"
    #: Config keys read without an annotation: the DHCP layers.
    _CONFIG_KEYS = frozenset({"dhcp_options", "dhcp_when", "dhcp_complete"})

    def __init__(self, **kwargs: object) -> None:
        for prop, key in {
            "dhcpzone": "",
            "mac": "",
            "ip": "",
            "image": "",
            "hostname": "",
            "template_path": [],
        }.items():
            kwargs.setdefault(prop, key)
        super().__init__(**kwargs)
        if isinstance(self.mac, (int, float)):
            # YAML 1.1 reads an unquoted all-digit MAC as a base-60 number,
            # which would parse as some other machine's MAC.
            raise PixieConfigError(
                f"target {self._id!r}: mac {self.mac!r} is a number, not a MAC; "
                "quote it (YAML reads an unquoted 52:54:00:12:34:56 as base 60)"
            )
        # If no MAC was given but the id itself is a MAC, adopt it; otherwise
        # fall back to the null MAC so downstream `.mac` is always a MACAddress.
        if not self.mac or str(self.mac) == self._NULL_MAC:
            if MACAddress.is_valid(str(self._id)):
                self.mac = self._id
            else:
                self.mac = self._NULL_MAC
        if not isinstance(self.mac, MACAddress):
            self.mac = MACAddress(self.mac)
        id_str = str(getattr(self, "_id", "") or "")
        id_is_mac = MACAddress.is_valid(id_str)
        id_is_ip = netimps.is_valid(id_str, IPAddress)
        if not self.ip and id_is_ip:
            self.ip = id_str
        if not self.hostname and not id_is_mac and not id_is_ip:
            self.hostname = id_str
        self.ip = netimps.parse(self.ip, IPAddress) if self.ip else ""
        self.hostname = self.hostname.lower()
        self._ip_looked_up = False

    def resolve_ip(self) -> "PixieTarget":
        """Fill an unknown `ip` from `hostname`: one forward lookup, ever.

        Never retried: a name that does not resolve answers the same next
        time, so it leaves `ip` unset with a warning, and never raises.
        """
        if self.ip or not self.hostname or self._ip_looked_up:
            return self
        self._ip_looked_up = True
        try:
            resolved = netimps.resolve(self.hostname)
        except ValueError as exc:  # malformed name: not worth killing the run
            LOGGER.warning(
                "target %s: %r is not resolvable: %s", self._id, self.hostname, exc
            )
            return self
        if resolved:
            self.ip = netimps.parse(resolved[0], IPAddress)
        else:
            LOGGER.warning(
                "target %s: hostname %r did not resolve; its ip stays unset",
                self._id,
                self.hostname,
            )
        return self


class PixieImage(Resource):
    """What a target boots, as configured under `images:`.

    Also a `content.Resource`, so it carries `src`/`path` addressing its
    artifacts in a repository. Those play no part in template lookup.
    """

    template_path: list["Path"]
    globals: dict
    _CONFIG_KEYS = PixieTarget._CONFIG_KEYS

    def __init__(self, **kwargs: object) -> None:
        # `searchpaths` reads this on every render, so an image that simply
        # does not declare one must still have the empty list.
        kwargs.setdefault("template_path", [])
        super().__init__(**kwargs)

    def match(self, name: str, check: str) -> object:
        """Does image `name` serve `check`? Override point for image selection.

        Return a bool, or a comparable score so `lookup_image` can prefer the best
        of several matches. Anything falsy means "not this one".
        """
        return name == check
