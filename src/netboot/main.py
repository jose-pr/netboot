"""The ``pixie`` command-line application.

A thin driver over :func:`duho.app`. ``app`` owns command discovery, parser
build, per-command ``register``, config/env layering, parsing and logging setup.
The one piece netboot overrides is *dispatch*: netboot loads the layered YAML config
into a single :class:`~netboot.Pixie` object, then runs the selected command against
it.

A netboot command module exposes ``run(netboot, args, conf)`` -- ``netboot`` is the built
:class:`~netboot.Pixie`, ``args`` the parsed globals, ``conf`` the raw merged config
dict. That is why dispatch invokes the module's ``run`` itself (with a netboot-first
signature) rather than duho's :func:`~duho.run_command` (which passes only args).
"""

from __future__ import annotations

import os as _os
import typing as _ty
from copy import deepcopy as _deepcopy
from importlib import import_module as _import_module

from duho import Arg, Cli, Extend, LoggingArgs, app, parse_globals
from jinja2 import TemplateError as _TemplateError
from duho.discovery import ModuleCommand, discover_commands
from duho.env import Env
from pathlib_next import LocalPath, Path, UriPath

from . import Pixie, PixieConfigError, PixieError, __version__
from .logging import LOGGER, quiet_noisy_dependencies
from .utils.misc import parse_path  # re-exported: the CLI's documented helper

#: Package import path to the built-in command modules.
_BUILTIN_COMMANDS = "netboot.cmds"


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


def _discover(argv: "_ty.Sequence[str] | None") -> "list":
    """Resolve the command set passed to :func:`duho.app` as ``commands=``.

    Built-ins first, then ``--cmdspath`` entries, which have to be honoured
    pre-parse and so cannot be left to duho. Later sources win on a name clash
    (a user command shadows a built-in), then the list is de-duplicated by
    subcommand name preserving that precedence.

    ``PIXIE_CMDS_PATH`` is deliberately *not* read here: since duho 0.4.1 the
    env-derived ``CMDS_PATH`` is a layer :func:`duho.app` always merges on top
    of whatever ``commands=`` it is handed, so resolving it here too would
    discover the same modules twice.
    """
    globals_ = parse_globals(Pixie_, argv)
    sources: "list[str]" = [_BUILTIN_COMMANDS]
    sources += list(globals_.cmdspath or [])

    by_name: "dict[str, object]" = {}
    for source in sources:
        if not source:
            continue
        for command in discover_commands(source):
            name = getattr(command, "_parsername_", None) or getattr(
                command, "__name__", None
            )
            if name:
                by_name[name] = command  # later source wins
    return list(by_name.values())


def _load_config(args: "Pixie_") -> dict:
    """Load and merge the netboot YAML config into a dict.

    Mirrors the original loader: an explicit ``--config`` file, else ``pixie.yaml``
    under ``--baseconfig`` (default ``./config``). ``conf['templates']`` gets the
    CWD ``templates`` dir prepended and every entry coerced to a :class:`Path`.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "reading a netboot config file requires the 'config' extra: "
            "pip install netboot[config]"
        ) from exc
    from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

    cwd = LocalPath(_os.getcwd())

    if args.config:
        path = parse_path(args.config)
        # Load the file by name with its own directory as the include base_dir.
        # (Using .name avoids relative_to raising for absolute/URI paths.)
        baseconfig: Path = path.parent
        configs = [path.name]
    else:
        baseconfig = (
            parse_path(args.baseconfig) if args.baseconfig else (cwd / "config")
        )
        configs = ["pixie.yaml"]

    loader = ConfigLoader(
        base_dir=baseconfig,
        interpolate=True,
        recursive=True,
        merge=ConfigLoaderMergeMethod.Deep,
        # Hardened on purpose. A netboot config is assembled from `!include`s
        # that often come from inventory exports rather than from the operator's
        # own hand, and netboot has never documented running commands from a
        # config document. `allow_commands=False` drops the command source and
        # `sandbox=True` renders interpolation in jinja2's sandbox, so a value
        # cannot reach out of the template language. Restricting *where*
        # includes may read from is a separate policy: set
        # `YACONFIGLIB_CONFINE_TO` to confine them to a directory.
        allow_commands=False,
        sandbox=True,
    )
    # yaconfiglib auto-registers !include/!load on the active loader class during
    # load(); a manual yaml.add_constructor is redundant (and since yaconfiglib
    # 0.10.0 it warns that it overrides the built-in handler).
    conf = loader.load(*configs)

    # An empty or comment-only file loads as None, and a file whose top level
    # is a list or a scalar is a config mistake: say so here instead of
    # failing later with an AttributeError from inside the engine.
    if conf is None:
        conf = {}
    if not isinstance(conf, dict):
        raise PixieConfigError(
            f"config must be a mapping at the top level, got "
            f"{type(conf).__name__}: {baseconfig}/{configs[0]}"
        )

    templates = conf.setdefault("templates", [])
    if templates is None:
        templates = conf["templates"] = []
    elif not isinstance(templates, list):
        # `templates: some/dir` is the obvious way to write a single path.
        templates = conf["templates"] = [templates]
    templates.insert(0, cwd / "templates")
    for idx, template in enumerate(templates):
        if not isinstance(template, Path):
            templates[idx] = parse_path(template)
    return conf


def _entrypoint(command: ModuleCommand) -> "_ty.Callable | None":
    """The function duho would call for this module command.

    duho resolves `main`, then `run`, then `call`, and remembers the result.
    Looking only at `module.run` (as this used to) missed a command whose body
    is `main`, and then handed it to duho's single-argument dispatch.
    """
    resolved = getattr(command, "_entrypoint", None)
    if callable(resolved):
        return resolved
    module = getattr(command, "module", None)
    for name in ("main", "run", "call"):
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


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
    """duho ``app`` dispatch seam: build ``Pixie`` from config and run the command.

    A netboot command is always a module command exposing ``run(netboot, args, conf)``.
    We build a single :class:`~netboot.Pixie` from the layered config, then invoke
    the selected module's ``run``. A non-module command (none today) falls back
    to duho's own single dispatch.
    """
    from duho import run_command

    if not isinstance(command, ModuleCommand):
        return run_command(_ty.cast(_ty.Any, command), instance)

    # A user command discovered via --cmdspath/PIXIE_CMDS_PATH may follow duho's plain
    # 1-arg run(args) contract rather than netboot's run(netboot, args, conf); only the
    # netboot-first contract needs a built Pixie, so introspect before building one.
    run = _entrypoint(command)
    if not _wants_netboot(run):
        return run_command(command, instance)

    for name in instance.load_module or []:
        if name:
            _import_module(name)

    conf = _load_config(instance)
    orig = _deepcopy(conf)
    netboot = Pixie(**conf)

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


#: Exceptions an operator can trigger with bad input. Reported as one line;
#: anything else keeps its traceback, because it is netboot's bug to fix.
#: `TemplateError` covers both a broken template and the sandbox refusing an
#: expression in a config value (`SecurityError`).
_USER_ERRORS = (
    PixieError,
    FileNotFoundError,
    NotADirectoryError,
    PermissionError,
    _TemplateError,
)


def main(
    name: "str | None" = None,
    argv: "_ty.Sequence[str] | None" = None,
) -> int:
    """Build the app, parse ``argv``, and run the selected command.

    ``name`` (default ``"pixie"``) is both the prog name and the environment
    prefix: settings are read through :class:`duho.env.Env`, so ``PIXIE_<KEY>``
    variables (and an optional ``pixie_env`` companion module of defaults) apply.
    The resolved ``Env`` is attached to the dispatched instance as ``_env_``.

    A configuration or lookup error is reported as a single line and exits 2;
    the traceback is logged at DEBUG, so ``-v`` still shows it. Other
    exceptions propagate untouched.
    """
    name = name or "pixie"
    env = Env(name)
    # The CLI is an application, so it may quiet noisy dependencies; importing
    # the library does not do this for its host.
    quiet_noisy_dependencies()
    try:
        return app(
            Pixie_,
            commands=_discover(argv),
            argv=argv,
            name=name,
            description=Pixie_.__doc__,
            env=env,
            dispatch=_dispatch,
        )
    except _USER_ERRORS as exc:
        LOGGER.error("%s", exc)
        LOGGER.debug("%s", exc, exc_info=True)
        return 2
    except yaml_error() as exc:  # malformed YAML: the file and line are in `exc`
        LOGGER.error("could not parse the configuration: %s", exc)
        LOGGER.debug("%s", exc, exc_info=True)
        return 2


def yaml_error() -> "type[BaseException]":
    """``yaml.YAMLError`` if PyYAML is installed, else an unraisable placeholder."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - only without the config extra

        class _NoYamlError(Exception): ...

        return _NoYamlError
    return yaml.YAMLError


if __name__ == "__main__":
    raise SystemExit(main())
