"""The ``netboot`` command-line application.

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
from duho.discovery import ModuleCommand, discover_commands
from pathlib_next import LocalPath, Path, UriPath

from . import Pixie, __version__

#: Package import path to the built-in command modules.
_BUILTIN_COMMANDS = "netboot.cmds"


def parse_path(path: "str | Path") -> Path:
    """Parse a config/template path: a bare path is local, ``scheme:`` is a URI."""
    if isinstance(path, Path):
        return path
    if ":" not in path:
        return LocalPath(path)
    return UriPath(path)


class PixieArgs(LoggingArgs):
    """Global options shared by every ``netboot`` command.

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
    """Netboot: PXE provisioning management."""

    _version_ = __version__


def _discover(argv: "_ty.Sequence[str] | None") -> "list":
    """Resolve the command set: built-ins, then env/CLI-provided paths.

    Later sources win on a name clash (a user command shadows a built-in), then
    the list is de-duplicated by subcommand name preserving that precedence.
    """
    globals_ = parse_globals(Pixie_, argv)
    sources: "list[str]" = [_BUILTIN_COMMANDS]
    if _os.environ.get("NETBOOT_PATH"):
        sources += _os.environ["NETBOOT_PATH"].split(_os.pathsep)
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

    Mirrors the original loader: an explicit ``--config`` file, else ``netboot.yaml``
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
        baseconfig = parse_path(args.baseconfig) if args.baseconfig else (cwd / "config")
        configs = ["netboot.yaml"]

    loader = ConfigLoader(
        base_dir=baseconfig,
        interpolate=True,
        recursive=True,
        merge=ConfigLoaderMergeMethod.Deep,
    )
    # yaconfiglib auto-registers !include/!load on the active loader class during
    # load(); a manual yaml.add_constructor is redundant (and yaconfiglib >=0.11
    # warns that it overrides the built-in handler).
    conf: dict = loader.load(*configs)

    templates: "list" = conf.setdefault("templates", [])
    templates.insert(0, cwd / "templates")
    for idx, template in enumerate(templates):
        if not isinstance(template, Path):
            templates[idx] = parse_path(template)
    return conf


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

    # A user command discovered via --cmdspath/NETBOOT_PATH may follow duho's plain
    # 1-arg run(args) contract rather than netboot's run(netboot, args, conf); only the
    # netboot-first contract needs a built Pixie, so introspect before building one.
    run = getattr(command.module, "run", None)
    if not _wants_netboot(run):
        return run_command(command, instance)

    for name in instance.load_module or []:
        if name:
            _import_module(name)

    conf = _load_config(instance)
    orig = _deepcopy(conf)
    netboot = Pixie(**conf)

    result = run(netboot, instance, orig)
    return 0 if result is None else int(result)


def main(
    name: "str | None" = None,
    argv: "_ty.Sequence[str] | None" = None,
) -> int:
    """Build the app, parse ``argv``, and run the selected command."""
    name = name or "netboot"
    return app(
        Pixie_,
        commands=_discover(argv),
        argv=argv,
        name=name,
        description=Pixie_.__doc__,
        dispatch=_dispatch,
    )


if __name__ == "__main__":
    raise SystemExit(main())
