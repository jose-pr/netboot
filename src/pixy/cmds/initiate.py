"""Initiate the PXE process for a target: render artifacts and arm DHCP."""

from __future__ import annotations

import argparse

from pixy import Pixy
from pixy.logging import LOGGER


def register(parser: argparse.ArgumentParser, args) -> None:
    parser.add_argument(
        "target", help="-/Hostname/MAC/IP of the target to initiate"
    )
    parser.add_argument(
        "--iscsi",
        action="store_true",
        default=False,
        help="Prepare as an iSCSI LUN",
    )


def run(pixy: Pixy, args, conf: dict) -> int:
    target = pixy.lookup_target(args.target)
    if target is None:
        LOGGER.error("Target not found: %s", args.target)
        return 1
    LOGGER.info(
        "Initiating Hostname: %s IP: %s MAC: %s",
        target.hostname,
        target.ip,
        target.mac,
    )
    pixy.initialize(target)
    LOGGER.info("Pixy initiated for: %s", args.target)
    return 0
