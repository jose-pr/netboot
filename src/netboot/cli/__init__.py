"""The `pixie` command line, built on duho.

`main` is the entry point of the `pixie` console script and of `python -m
netboot`. Each built-in command is a class in a module of its own; a
`--cmdspath` plugin may still be a module with `run(netboot, args, conf)`.
"""

from __future__ import annotations

import typing as _ty

from duho import run as _run
from duho.env import Env

from ..logging import quiet_noisy_dependencies
from ._common import Command
from ._root import _USER_ERRORS, Pixie_, _discover, _dispatch, _report_error

__all__ = ["Command", "main"]


def main(
    argv: "_ty.Sequence[str] | None" = None,
    *,
    name: "str | None" = None,
) -> int:
    """Build the app, parse ``argv``, and run the selected command.

    ``name`` (default ``"pixie"``) is both the prog name and the environment
    prefix: settings are read through :class:`duho.env.Env`, so ``PIXIE_<KEY>``
    variables apply. The resolved ``Env`` is attached to the dispatched instance
    as ``_env_``.

    Goes through :func:`duho.run` with ``exit="parse"``: it returns the status,
    except that ``--help``, ``--version`` and a usage error raise ``SystemExit``.
    A configuration or lookup error is reported as a single line and exits 2;
    the traceback is logged at DEBUG, so ``-v`` still shows it. Other
    exceptions propagate untouched.
    """
    name = name or "pixie"
    # No companion `pixie_env` module: duho would import it from sys.path,
    # which under `python -m netboot` is the working directory.
    env = Env(name, autoload=False)
    # The CLI is an application, so it may quiet noisy dependencies; importing
    # the library does not do this for its host.
    quiet_noisy_dependencies()
    try:
        commands = _discover(argv)
    except _USER_ERRORS as exc:
        # Discovery happens before duho dispatches, so duho does not see this.
        _report_error(str(exc), 2, exc)
        return 2
    return _run(
        Pixie_,
        argv,
        exit="parse",
        commands=commands,
        name=name,
        description=Pixie_.__doc__,
        env=env,
        dispatch=_dispatch,
    )
