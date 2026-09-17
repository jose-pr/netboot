"""Initiate the PXE process for a target: arm DHCP for it."""

from __future__ import annotations

import argparse

from netboot import Pixie
from netboot.logging import LOGGER


def register(parser: argparse.ArgumentParser, args) -> None:
    parser.add_argument(
        "target", help="Id, hostname, MAC or IP of the target to initiate"
    )
    parser.add_argument(
        "--iscsi",
        action="store_true",
        default=False,
        help="Prepare as an iSCSI LUN (read by hooks; the built-in logic ignores it)",
    )


def run(netboot: Pixie, args, conf: dict) -> int:
    try:
        target = netboot.lookup_target(args.target)
    except LookupError as exc:  # more than one target matches: refuse, never guess
        LOGGER.error("%s", exc)
        return 1
    if target is None:
        LOGGER.error("Target not found: %s", args.target)
        return 1
    LOGGER.info(
        "Initiating Hostname: %s IP: %s MAC: %s",
        target.hostname,
        target.ip,
        target.mac,
    )
    netboot.initialize(target)
    LOGGER.info("Netboot initiated for: %s", args.target)
    return 0
