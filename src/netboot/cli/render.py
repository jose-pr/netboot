"""`pixie render`: a target's artifacts, to standard output or a directory."""

from __future__ import annotations

import sys
import typing as _ty
from pathlib import Path

from duho import UsageError

from ..logging import LOGGER
from ._common import Command

__all__ = ["Render"]


class Render(Command):
    """Render a target's artifacts, arming nothing."""

    _parsername_ = "render"

    target: str
    "Id, hostname, MAC or IP of the target"
    ("target",)

    names: _ty.List[str]
    "Artifact to render, found by the template search (boot.cfg); repeat for more"
    ("names",)

    out: _ty.Optional[str] = None
    "Write each artifact to DIR/NAME instead of standard output"
    ("--out",)

    def __call__(self) -> int:
        if not self.names:
            raise UsageError("give at least one artifact name")
        target = self.find(self.target)
        if target is None:
            return 1
        ctx = self.netboot.make_context(target)
        for name in self.names:
            # Text, or bytes for a byte-exact copy, which is never decoded.
            result = ctx.render(name)
            self.write(
                name, result if isinstance(result, bytes) else result.encode("utf-8")
            )
        return 0

    def write(self, name: str, data: bytes) -> None:
        """`data` to standard output, or to `--out`/NAME."""
        if self.out is None:
            sys.stdout.flush()
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            return
        path = Path(self.out) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        LOGGER.info("rendered %s for %s: %s", name, self.target, path)
