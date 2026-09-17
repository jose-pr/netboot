"""netboot: PXE provisioning management.

The public surface. The engine itself lives in :mod:`netboot.engine`; this
module re-exports it, so `from netboot import Pixie` is the supported import
either way.
"""

# Imported for its side effect: importing the scheme modules is what registers
# `http://`, `file://`, `tftp://` and friends with pathlib_next's URI parser.
import pathlib_next.uri.schemes  # noqa: F401

from ._version import __version__
from .content import Repository, Resource
from .dhcp import DhcpServer, DhcpZone
from .engine import (
    Pixie,
    PixieConfigError,
    PixieContext,
    PixieError,
    PixieEvent,
    PixieImage,
    PixieLookupError,
    PixieTarget,
)
from .logging import LOGGER
from .utils import net as netutils

__all__ = [
    "Pixie",
    "PixieContext",
    "PixieEvent",
    "PixieImage",
    "PixieTarget",
    "PixieError",
    "PixieConfigError",
    "PixieLookupError",
    "DhcpServer",
    "DhcpZone",
    "Repository",
    "Resource",
    "LOGGER",
    "netutils",
    "__version__",
]
