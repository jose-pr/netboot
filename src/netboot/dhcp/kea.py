"""Kea backend: `reservation-add` / `reservation-del` through the control agent.

Those commands come from Kea's **host_cmds** hook library (open source since
ISC opened it), and they need a hosts backend that is writable — a file-only Kea
will load the library and still refuse. That is a precondition to report
clearly, not to work around.

The reservation's ``subnet-id`` is a property of the *network*, so it is
resolved when a target is applied — from the zone, or by matching the zone's
network against the server's own config — never from the connection string.
"""

from __future__ import annotations

import typing as _ty
from urllib.parse import urlsplit

from ..logging import LOGGER
from . import DhcpServer

#: Reservation *fields* in Kea's model: they are not `option-data` entries.
_FIELDS = {
    "next-server": "next-server",
    "boot-file-name": "boot-file-name",
    "host-name": "hostname",
}


class kea(DhcpServer):  # noqa: N801 - the class name is the URI scheme
    """`kea://[user:pass@]host:8000/?service=dhcp4` (`keas://` for https).

    Client options ride in the query; `subnet_id` does not — it belongs to the
    zone, because one Kea serves many subnets.
    """

    SETTINGS = frozenset({"service"})

    def __init__(self, uri: str):
        super().__init__(uri)
        parts = urlsplit(uri)
        scheme = "https" if parts.scheme == "keas" else "http"
        port = parts.port or 8000
        self.endpoint = f"{scheme}://{parts.hostname or 'localhost'}:{port}/"
        self.auth = (parts.username, parts.password or "") if parts.username else None
        self.service = self.settings.get("service", "dhcp4")
        self._subnet_ids: "dict[str, int]" = {}

    # -- transport --------------------------------------------------------

    def _session(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise ImportError(
                "the Kea backend requires netboot's 'kea' extra: "
                "pip install netboot[kea]"
            ) from exc
        return requests

    def command(self, command: str, arguments: "dict|None" = None) -> dict:
        """Send one command and return its (single) response object."""
        from .. import PixieConfigError

        requests = self._session()
        payload = {"command": command, "service": [self.service]}
        if arguments is not None:
            payload["arguments"] = arguments
        LOGGER.debug("kea %s -> %s", command, self.endpoint)
        response = requests.post(
            self.endpoint, json=payload, auth=self.auth, timeout=30
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list) or not body:
            raise PixieConfigError(
                f"kea returned no response object for {command!r}: {body!r}"
            )
        return body[0]

    # -- the DhcpServer contract ------------------------------------------

    def add_target(self, netboot: "_ty.Any"):
        from .. import PixieConfigError

        reservation = self._reservation(netboot)
        result = self.command("reservation-add", {"reservation": reservation})
        code = result.get("result")
        if code == 0:
            return
        text = result.get("text", "")
        if code == 1 and "exist" in text.lower():
            raise PixieConfigError(
                f"kea already holds a reservation for {reservation['hw-address']} "
                f"in subnet {reservation['subnet-id']}: {text}. Remove it, or run "
                "`pixie complete` for the target that owns it — netboot will not "
                "overwrite a reservation it did not make."
            )
        raise PixieConfigError(_explain(command="reservation-add", result=result))

    def remove_target(self, netboot: "_ty.Any"):
        from .. import PixieConfigError

        target = netboot.target
        arguments = {
            "subnet-id": self._subnet_id(netboot),
            "identifier-type": "hw-address",
            "identifier": str(target.mac),
        }
        result = self.command("reservation-del", arguments)
        code = result.get("result")
        if code in (0, 3):  # 3 = "not found", which is success for cleanup
            return
        raise PixieConfigError(_explain(command="reservation-del", result=result))

    # -- building the reservation -----------------------------------------

    def _reservation(self, ctx) -> dict:
        from .. import PixieLookupError
        from ..engine import PixieTarget

        target = ctx.target
        mac = str(target.mac)
        if not mac or mac == PixieTarget._NULL_MAC:
            raise PixieLookupError(
                f"target {target._id!r} has no MAC address, and a Kea reservation "
                "is identified by one"
            )

        options = self.options_for(ctx)
        reservation: "dict[str, _ty.Any]" = {
            "subnet-id": self._subnet_id(ctx),
            "hw-address": mac,
        }
        if target.ip:
            reservation["ip-address"] = str(target.ip)

        option_data = []
        for name, value in options.items():
            field = _FIELDS.get(name)
            if field:
                reservation[field] = _value(value)
                continue
            if name.startswith("option-") or name.isdigit():
                code = name[len("option-") :] if name.startswith("option-") else name
                option_data.append({"code": int(code), "data": _value(value)})
            else:
                option_data.append({"name": name, "data": _value(value)})
        option_data.extend(options.raw_for("kea"))
        if option_data:
            reservation["option-data"] = option_data
        return reservation

    def _subnet_id(self, ctx) -> int:
        """The subnet this zone is, resolved once per zone.

        Order: the zone's own `subnet_id`, else a `config-get` matched against
        the zone's network. Never a guess — a reservation in the wrong subnet
        silently never matches.
        """
        from .. import PixieConfigError

        zone = ctx.dhcpzone
        zone_id = str(getattr(zone, "_id", "") or "")
        declared = getattr(zone, "subnet_id", None)
        if declared is not None:
            return int(declared)
        if zone_id in self._subnet_ids:
            return self._subnet_ids[zone_id]

        network = getattr(zone, "network", None)
        if network is not None:
            config = self.command("config-get").get("arguments", {})
            subnets = config.get(f"Dhcp{self.service[-1]}", {}).get("subnet4", [])
            for subnet in subnets:
                if str(subnet.get("subnet", "")) == str(network):
                    found = int(subnet["id"])
                    self._subnet_ids[zone_id] = found
                    LOGGER.debug("kea subnet-id %s for zone %s", found, zone_id)
                    return found

        raise PixieConfigError(
            f"kea: no subnet-id for zone {zone_id!r} (network "
            f"{network!r}). Set `subnet_id` on the zone, or add a matching "
            "subnet4 entry to Kea — a reservation in the wrong subnet never "
            "matches, so netboot will not guess one."
        )


class keas(kea):  # noqa: N801 - the class name is the URI scheme
    """`keas://...` — the same backend, reached over https."""


def _value(value) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _explain(command: str, result: dict) -> str:
    """A Kea error, plus the cause an operator most often actually has."""
    text = result.get("text", "")
    message = f"kea refused {command}: {text or result!r}"
    if result.get("result") == 2:
        message += (
            " (result 2 is 'unsupported': the host_cmds hook library is probably "
            "not loaded, or the hosts backend is file-only or read-only, which "
            "cannot take live reservations)"
        )
    return message
