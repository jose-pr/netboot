"""Complete the PXE process for a target: run post-boot cleanup tasks."""

from __future__ import annotations

import argparse

from piskie import Piskie
from piskie.logging import LOGGER


def register(parser: argparse.ArgumentParser, args) -> None:
    parser.add_argument(
        "target", help="-/Hostname/MAC/IP of the target to complete"
    )


def run(piskie: Piskie, args, conf: dict) -> int:
    target = piskie.lookup_target(args.target)
    if target is None:
        LOGGER.error("Target not found: %s", args.target)
        return 1
    LOGGER.info("Running post-PXE tasks for: %s", args.target)
    piskie.complete(target)
    return 0
