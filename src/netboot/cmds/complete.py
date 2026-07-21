"""Complete the PXE process for a target: run post-boot cleanup tasks."""

from __future__ import annotations

import argparse

from netboot import Pixie
from netboot.logging import LOGGER


def register(parser: argparse.ArgumentParser, args) -> None:
    parser.add_argument(
        "target", help="-/Hostname/MAC/IP of the target to complete"
    )


def run(netboot: Pixie, args, conf: dict) -> int:
    target = netboot.lookup_target(args.target)
    if target is None:
        LOGGER.error("Target not found: %s", args.target)
        return 1
    LOGGER.info("Running post-PXE tasks for: %s", args.target)
    netboot.complete(target)
    return 0
