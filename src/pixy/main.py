"""The ``pixy`` command-line application.

A thin driver over :func:`duho.app`. ``app`` owns command discovery, parser
build, per-command ``register``, config/env layering, parsing and logging setup.
The one piece pixy overrides is *dispatch*: pixy loads the layered YAML config
into a single :class:`~pixy.Pixy` object, then runs the selected command against
it.

A pixy command module exposes ``run(pixy, args, conf)`` -- ``pixy`` is the built
:class:`~pixy.Pixy`, ``args`` the parsed globals, ``conf`` the raw merged config
dict. That is why dispatch invokes the module's ``run`` itself (with a pixy-first
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

from . import Pixy

#: Package import path to the built-in command modules.
_BUILTIN_COMMANDS = "pixy.cmds"


def parse_path(path: "str | Path") -> Path:
    """Parse a config/template path: a bare path is local, ``scheme:`` is a URI."""
    if isinstance(path, Path):
        return path
    if ":" not in path:
        return LocalPath(path)
    return UriPath(path)


class PixyArgs(LoggingArgs):
    """Global options shared by every ``pixy`` command.

    A data mixin (:class:`duho.LoggingArgs`): it carries the global fields;
    :class:`Pixy_` combines it with :class:`duho.Cli` to make the runnable app
    root.
    """

    config: "_ty.Optional[str]" = None
    "Alternate configuration file (a yaml or cfg); overrides --baseconfig discovery"
    ("--config", "-c")  # type: ignore

    baseconfig: "_ty.Optional[str]" = None
    "Base config directory to search (default: ./config)"

    load_module: "Arg[list[str], Extend(':')]" = []
    "Python module(s) to import before building pixy (config/hook deps)"
    ("--load-module", "-l")  # type: ignore

    cmdspath: "Arg[list[str], Extend(_os.pathsep)]" = []
    "Extra directories/packages to search for commands"
    ("--cmdspath",)  # type: ignore


class Pixy_(PixyArgs, Cli):
    """Pixy: PXE provisioning management."""

    _version_ = "0.1.0"


def _discover(argv: "_ty.Sequence[str] | None") -> "list":
    """Resolve the command set: built-ins, then env/CLI-provided paths.

    Later sources win on a name clash (a user command shadows a built-in), then
    the list is de-duplicated by subcommand name preserving that precedence.
    """
    globals_ = parse_globals(Pixy_, argv)
    sources: "list[str]" = [_BUILTIN_COMMANDS]
    if _os.environ.get("PIXY_PATH"):
        sources += _os.environ["PIXY_PATH"].split(_os.pathsep)
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


def _load_config(args: "Pixy_") -> dict:
    """Load and merge the pixy YAML config into a dict.

    Mirrors the original loader: an explicit ``--config`` file, else ``pixy.yaml``
    under ``--baseconfig`` (default ``./config``). ``conf['templates']`` gets the
    CWD ``templates`` dir prepended and every entry coerced to a :class:`Path`.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError(
            "reading a pixy config file requires the 'config' extra: "
            "pip install pixy[config]"
        ) from exc
    from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

    cwd = LocalPath(_os.getcwd())

    if args.config:
        path = parse_path(args.config)
        baseconfig: Path = path.parent
        configs = [path.relative_to(baseconfig)]
    else:
        baseconfig = parse_path(args.baseconfig) if args.baseconfig else (cwd / "config")
        configs = ["pixy.yaml"]

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


def _dispatch(command: object, instance: "Pixy_") -> int:
    """duho ``app`` dispatch seam: build ``Pixy`` from config and run the command.

    A pixy command is always a module command exposing ``run(pixy, args, conf)``.
    We build a single :class:`~pixy.Pixy` from the layered config, then invoke
    the selected module's ``run``. A non-module command (none today) falls back
    to duho's own single dispatch.
    """
    if not isinstance(command, ModuleCommand):
        from duho import run_command

        return run_command(_ty.cast(_ty.Any, command), instance)

    for name in instance.load_module or []:
        if name:
            _import_module(name)

    conf = _load_config(instance)
    orig = _deepcopy(conf)
    pixy = Pixy(**conf)

    result = command.module.run(pixy, instance, orig)
    return 0 if result is None else int(result)


def main(
    name: "str | None" = None,
    argv: "_ty.Sequence[str] | None" = None,
) -> int:
    """Build the app, parse ``argv``, and run the selected command."""
    name = name or "pixy"
    return app(
        Pixy_,
        commands=_discover(argv),
        argv=argv,
        name=name,
        description=Pixy_.__doc__,
        dispatch=_dispatch,
    )


if __name__ == "__main__":
    raise SystemExit(main())
