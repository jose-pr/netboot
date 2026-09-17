"""Complete the PXE process for a target: disarm its DHCP backends."""

from __future__ import annotations

import argparse

from netboot import Pixie
from netboot.logging import LOGGER


def register(parser: argparse.ArgumentParser, args) -> None:
    """Add this command's arguments to its subparser."""
    parser.add_argument(
        "target", help="Id, hostname, MAC or IP of the target to complete"
    )


def run(netboot: Pixie, args, conf: dict) -> int:
    """Look up the target and disarm its DHCP. Exit 1 if it cannot be found."""
    try:
        target = netboot.lookup_target(args.target)
    except LookupError as exc:  # more than one target matches: refuse, never guess
        LOGGER.error("%s", exc)
        return 1
    if target is None:
        LOGGER.error("Target not found: %s", args.target)
        return 1
    LOGGER.info("Running post-PXE tasks for: %s", args.target)
    netboot.complete(target)
    return 0
