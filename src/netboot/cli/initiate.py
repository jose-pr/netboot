"""`pixie initiate`: arm DHCP for a target."""

from __future__ import annotations

from ..logging import LOGGER
from ._common import Command

__all__ = ["Initiate"]


class Initiate(Command):
    """Arm DHCP for a target; hooks on its events can render and run more."""

    _parsername_ = "initiate"

    target: str
    "Id, hostname, MAC or IP of the target to initiate"
    ("target",)

    iscsi: bool = False
    "Prepare as an iSCSI LUN (read by hooks; the built-in logic ignores it)"
    ("--iscsi",)

    def __call__(self) -> int:
        target = self.find(self.target)
        if target is None:
            return 1
        LOGGER.info(
            "Initiating Hostname: %s IP: %s MAC: %s",
            target.hostname,
            target.ip,
            target.mac,
        )
        self.netboot.initialize(target, iscsi=self.iscsi)
        LOGGER.info("Netboot initiated for: %s", self.target)
        return 0
