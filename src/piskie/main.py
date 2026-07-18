"""The ``piskie`` command-line application.

A thin driver over :func:`duho.app`. ``app`` owns command discovery, parser
build, per-command ``register``, config/env layering, parsing and logging setup.
The one piece piskie overrides is *dispatch*: piskie loads the layered YAML config
into a single :class:`~piskie.Piskie` object, then runs the selected command against
it.

A piskie command module exposes ``run(piskie, args, conf)`` -- ``piskie`` is the built
:class:`~piskie.Piskie`, ``args`` the parsed globals, ``conf`` the raw merged config
dict. That is why dispatch invokes the module's ``run`` itself (with a piskie-first
signature) rather than duho's :func:`~duho.run_command` (which passes only args).
"""

from __future__ import annotations

import os as _os
import typing as _ty
from copy import deepcopy as _deepcopy
from importlib import import_module as _import_module

from duho import Arg, Cli, Extend, LoggingArgs, app, parse_globals
from duho.discovery import ModuleCommand, discover_commands
from pathlib_next import LocalPath, Path, UriPath

from . import Piskie, __version__

#: Package import path to the built-in command modules.
_BUILTIN_COMMANDS = "piskie.cmds"


def parse_path(path: "str | Path") -> Path:
    """Parse a config/template path: a bare path is local, ``scheme:`` is a URI."""
    if isinstance(path, Path):
        return path
    if ":" not in path:
        return LocalPath(path)
    return UriPath(path)


class PiskieArgs(LoggingArgs):
    """Global options shared by every ``piskie`` command.

    A data mixin (:class:`duho.LoggingArgs`): it carries the global fields;
    :class:`Piskie_` combines it with :class:`duho.Cli` to make the runnable app
    root.
    """

    config: "_ty.Optional[str]" = None
    "Alternate configuration file (a yaml or cfg); overrides --baseconfig discovery"
    ("--config", "-c")  # type: ignore

    baseconfig: "_ty.Optional[str]" = None
    "Base config directory to search (default: ./config)"

    load_module: "Arg[list[str], Extend(':')]" = []
    "Python module(s) to import before building piskie (config/hook deps)"
    ("--load-module", "-l")  # type: ignore

    cmdspath: "Arg[list[str], Extend(_os.pathsep)]" = []
    "Extra directories/packages to search for commands"
    ("--cmdspath",)  # type: ignore


class Piskie_(PiskieArgs, Cli):
    """Piskie: PXE provisioning management."""

    _version_ = __version__


def _discover(argv: "_ty.Sequence[str] | None") -> "list":
    """Resolve the command set: built-ins, then env/CLI-provided paths.

    Later sources win on a name clash (a user command shadows a built-in), then
    the list is de-duplicated by subcommand name preserving that precedence.
    """
    globals_ = parse_globals(Piskie_, argv)
    sources: "list[str]" = [_BUILTIN_COMMANDS]
    if _os.environ.get("PISKIE_PATH"):
        sources += _os.environ["PISKIE_PATH"].split(_os.pathsep)
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


def _load_config(args: "Piskie_") -> dict:
    """Load and merge the piskie YAML config into a dict.

    Mirrors the original loader: an explicit ``--config`` file, else ``piskie.yaml``
    under ``--baseconfig`` (default ``./config``). ``conf['templates']`` gets the
    CWD ``templates`` dir prepended and every entry coerced to a :class:`Path`.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "reading a piskie config file requires the 'config' extra: "
            "pip install piskie[config]"
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
        baseconfig = parse_path(args.baseconfig) if args.baseconfig else (cwd / "config")
        configs = ["piskie.yaml"]

    loader = ConfigLoader(
        base_dir=baseconfig,
        interpolate=True,
        recursive=True,
        merge=ConfigLoaderMergeMethod.Deep,
    )
    yaml.add_constructor("!include", loader, yaml.SafeLoader)
    conf: dict = loader.load(*configs)

    templates: "list" = conf.setdefault("templates", [])
    templates.insert(0, cwd / "templates")
    for idx, template in enumerate(templates):
        if not isinstance(template, Path):
            templates[idx] = parse_path(template)
    return conf


def _wants_piskie(run: "_ty.Callable | None") -> bool:
    """Does this command's ``run`` follow piskie's ``run(piskie, args, conf)`` shape?

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


def _dispatch(command: object, instance: "Piskie_") -> int:
    """duho ``app`` dispatch seam: build ``Piskie`` from config and run the command.

    A piskie command is always a module command exposing ``run(piskie, args, conf)``.
    We build a single :class:`~piskie.Piskie` from the layered config, then invoke
    the selected module's ``run``. A non-module command (none today) falls back
    to duho's own single dispatch.
    """
    from duho import run_command

    if not isinstance(command, ModuleCommand):
        return run_command(_ty.cast(_ty.Any, command), instance)

    # A user command discovered via --cmdspath/PISKIE_PATH may follow duho's plain
    # 1-arg run(args) contract rather than piskie's run(piskie, args, conf); only the
    # piskie-first contract needs a built Piskie, so introspect before building one.
    run = getattr(command.module, "run", None)
    if not _wants_piskie(run):
        return run_command(command, instance)

    for name in instance.load_module or []:
        if name:
            _import_module(name)

    conf = _load_config(instance)
    orig = _deepcopy(conf)
    piskie = Piskie(**conf)

    result = run(piskie, instance, orig)
    return 0 if result is None else int(result)


def main(
    name: "str | None" = None,
    argv: "_ty.Sequence[str] | None" = None,
) -> int:
    """Build the app, parse ``argv``, and run the selected command."""
    name = name or "piskie"
    return app(
        Piskie_,
        commands=_discover(argv),
        argv=argv,
        name=name,
        description=Piskie_.__doc__,
        dispatch=_dispatch,
    )


if __name__ == "__main__":
    raise SystemExit(main())
