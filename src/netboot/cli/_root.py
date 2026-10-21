"""The `pixie` root: global options, command discovery, dispatch, error policy.

`duho.run` owns parser build, per-command `register`, env layering, parsing and
logging setup. netboot adds two things: the built-in commands are classes here
and a `--cmdspath` plugin may be a module with `run(netboot, args, conf)`, which
dispatch calls with the engine built from the config.
"""

from __future__ import annotations

import os as _os
import traceback as _traceback
import typing as _ty
from copy import deepcopy as _deepcopy
from importlib import import_module as _import_module

from dhcpctl import DHCPCtlError as _DHCPCtlError
from duho import Arg, Cli, Extend, LoggingArgs, parse_globals
from duho.discovery import ModuleCommand, discover_commands
from duho.logging import traceback_enabled
from tplx import TplxError as _TplxError

from .. import Pixie, PixieConfigError, PixieError, __version__
from ..logging import LOGGER
from ._config import load_config

__all__ = ["PixieArgs", "Pixie_"]


#: Exceptions an operator can trigger with bad input. Reported as one line;
#: anything else keeps its traceback, because it is netboot's bug to fix.
#: A config that cannot be loaded is a `PixieConfigError` by then, and a policy
#: refusal a `PermissionError`; `TplxError` is a template that cannot be found
#: or rendered. `DHCPCtlError` is a DHCP server refusing, or a URI it cannot
#: use.
_USER_ERRORS = (
    PixieError,
    _DHCPCtlError,
    FileNotFoundError,
    NotADirectoryError,
    PermissionError,
    _TplxError,
)

#: Maps each of those to exit status 2 for duho, which reports it through
#: :func:`_report_error`.
_ERROR_STATUS = {exc_type: 2 for exc_type in _USER_ERRORS}


def _report_error(message: str, code: int, error: BaseException) -> None:
    """Report an operator error through netboot's logger, not duho's stderr line.

    The traceback goes to DEBUG, so ``-v`` still shows it, and to standard
    error when ``DUHO_TRACEBACK`` is on (duho's truthy values): duho leaves
    that to this hook.
    """
    LOGGER.error("%s", message)
    LOGGER.debug("%s", message, exc_info=error)
    if traceback_enabled():
        _traceback.print_exception(type(error), error, error.__traceback__)


class PixieArgs(LoggingArgs):
    """Global options shared by every ``pixie`` command.

    A data mixin (:class:`duho.LoggingArgs`): it carries the global fields;
    :class:`Pixie_` combines it with :class:`duho.Cli` to make the runnable app
    root.
    """

    config: "_ty.Optional[str]" = None
    "Alternate configuration file (a yaml or cfg); overrides --baseconfig discovery"
    ("--config", "-c")  # type: ignore

    baseconfig: "_ty.Optional[str]" = None
    "Base config directory to search (default: ./config)"

    load_module: "Arg[list[str], Extend(':')]" = []
    "Python module(s) to import before building netboot (config/hook deps)"
    ("--load-module", "-l")  # type: ignore

    cmdspath: "Arg[list[str], Extend(_os.pathsep)]" = []
    "Extra directories/packages to search for commands"
    ("--cmdspath",)  # type: ignore


class Pixie_(PixieArgs, Cli):
    """Pixie: PXE provisioning management."""

    _version_ = __version__
    #: Parser name: without it argparse reports errors and --version as
    #: "Pixie_", the class name, rather than the command the user typed.
    _parsername_ = "pixie"
    #: duho applies -v/-q/--loglevel to the logger of this name. Point it at
    #: netboot's own logger, or the verbosity flags adjust a logger nothing
    #: in this package ever writes to.
    _logger_name_ = "netboot"
    #: Operator errors a command lets escape end with status 2.
    _errors_ = _ERROR_STATUS
    _report_error_ = staticmethod(_report_error)


def _discover(argv: "_ty.Sequence[str] | None") -> "list":
    """Resolve the command set passed to :func:`duho.run` as ``commands=``.

    The built-in command classes first, then ``--cmdspath`` entries, which have
    to be honoured pre-parse and so cannot be left to duho. A ``--cmdspath``
    command shadows a built-in of its name and keeps its place; new names
    follow in discovery order.

    ``PIXIE_CMDS_PATH`` is deliberately *not* read here: since duho 0.4.1 the
    env-derived ``CMDS_PATH`` is a layer :func:`duho.app` always merges on top
    of whatever ``commands=`` it is handed, so resolving it here too would
    discover the same modules twice.
    """
    from .complete import Complete
    from .dhcp_config import DhcpConfig
    from .initiate import Initiate
    from .render import Render

    globals_ = parse_globals(Pixie_, argv)
    commands: "dict[str, object]" = {
        cls._parsername_: cls for cls in (Complete, DhcpConfig, Initiate, Render)
    }
    sources = [s for s in globals_.cmdspath or [] if s]
    for command in discover_commands(sources, sort=False) if sources else []:
        name = getattr(command, "_parsername_", None)
        if name in commands:
            LOGGER.debug("--cmdspath command %s shadows the built-in", name)
        commands[name] = command
    return list(commands.values())


def _wants_netboot(run: "_ty.Callable | None") -> bool:
    """Does this command's ``run`` follow netboot's ``run(netboot, args, conf)`` shape?

    True when ``run`` accepts at least three positional parameters (or has
    ``*args``); a plain duho ``run(args)`` returns False and is dispatched
    through duho's own ``run_command`` instead.
    """
    if run is None:
        return False
    import inspect as _inspect

    try:
        params = _inspect.signature(run).parameters.values()
    except (TypeError, ValueError):  # builtins / C funcs without a signature
        return True
    positional = 0
    for p in params:
        if p.kind is _inspect.Parameter.VAR_POSITIONAL:
            return True
        if p.kind in (
            _inspect.Parameter.POSITIONAL_ONLY,
            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 3


def _dispatch(command: object, instance: "Pixie_") -> int:
    """duho ``app`` dispatch seam: run a command, building ``Pixie`` for a plugin.

    A class command (the built-ins) builds its engine itself, so it runs through
    duho's own dispatch, as does a module command with duho's ``run(args)``. A
    module command with netboot's ``run(netboot, args, conf)`` is called with a
    :class:`~netboot.Pixie` built from the config.
    """
    from duho import run_command

    if not isinstance(command, ModuleCommand):
        return run_command(_ty.cast(_ty.Any, command), instance)

    # A user command discovered via --cmdspath/PIXIE_CMDS_PATH may follow duho's plain
    # 1-arg run(args) contract rather than netboot's run(netboot, args, conf); only the
    # netboot-first contract needs a built Pixie, so introspect before building one.
    run = command.entrypoint
    if not _wants_netboot(run):
        return run_command(command, instance)

    netboot, orig = build_engine(instance)

    result = run(netboot, instance, orig)
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    # The command already did its work; a return value we cannot use is worth a
    # warning, not a crash that reports failure after a successful run.
    LOGGER.warning(
        "command %r returned %r (%s), which is not an exit code; treating as success",
        getattr(command, "__name__", command),
        result,
        type(result).__name__,
    )
    return 0


def build_engine(instance: object) -> "tuple[Pixie, dict]":
    """Import `--load-module`s, load the config, build the engine.

    Returns the engine and a deep copy of the config it was built from, which a
    plugin's ``run(netboot, args, conf)`` receives as ``conf``.
    """
    for name in getattr(instance, "load_module", None) or []:
        if name:
            try:
                _import_module(name)
            except ImportError as exc:
                raise PixieConfigError(f"--load-module {name}: {exc}") from exc
    conf = load_config(
        getattr(instance, "config", None), getattr(instance, "baseconfig", None)
    )
    orig = _deepcopy(conf)
    return Pixie(**conf), orig
