"""`pixie complete`: disarm DHCP for a target."""

from __future__ import annotations

from ..logging import LOGGER
from ._common import Command

__all__ = ["Complete"]


class Complete(Command):
    """Disarm DHCP for a target once its install is done."""

    _parsername_ = "complete"

    target: str
    "Id, hostname, MAC or IP of the target to complete"
    ("target",)

    def __call__(self) -> int:
        target = self.find(self.target)
        if target is None:
            return 1
        LOGGER.info("Running post-PXE tasks for: %s", self.target)
        self.netboot.complete(target)
        return 0
