"""What every built-in command shares: the engine, and finding the target."""

from __future__ import annotations

import typing as _ty

from duho import Cmd

from ..logging import LOGGER

if _ty.TYPE_CHECKING:
    from .. import Pixie, PixieTarget

__all__ = ["Command"]


class Command(Cmd):
    """A `pixie` command. The global options arrive from the root parser."""

    #: The engine, built from the config on first use; a test may set it.
    _netboot_ = None

    @property
    def netboot(self) -> "Pixie":
        """The engine, from `--load-module`, `--config` and `--baseconfig`."""
        if self._netboot_ is None:
            from ._root import build_engine

            self._netboot_ = build_engine(self)[0]
        return self._netboot_

    def find(self, query: str) -> "PixieTarget | None":
        """The target `query` names, or None after logging why there is none.

        An ambiguous query is refused rather than guessed: arming the wrong
        machine is worse than arming none.
        """
        try:
            target = self.netboot.lookup_target(query)
        except LookupError as exc:
            LOGGER.error("%s", exc)
            return None
        if target is None:
            LOGGER.error("Target not found: %s", query)
        return target
