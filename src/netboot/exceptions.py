"""The errors netboot raises on purpose, all re-exported from `netboot`."""

from __future__ import annotations

__all__ = ["PixieError", "PixieConfigError", "PixieLookupError"]


class PixieError(Exception):
    """Base for the errors netboot raises on purpose.

    Separates "the operator gave us something unusable" from "netboot has a
    bug": the CLI reports the former as a one-line message and keeps the
    traceback for the latter.
    """


class PixieLookupError(PixieError, LookupError):
    """A target, image or zone could not be resolved, or the query was ambiguous."""


class PixieConfigError(PixieError, ValueError):
    """The configuration cannot be used as given."""
