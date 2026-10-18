"""Loading `pixie.yaml`: layered, includes confined by policy, roots resolved."""

from __future__ import annotations

import os as _os

from pathapi import LocalPath, Path

from ..exceptions import PixieConfigError
from ..utils.misc import parse_path

__all__ = ["load_config", "template_roots"]


def load_config(config: "str | None" = None, baseconfig: "str | None" = None) -> dict:
    """Load and merge the netboot YAML config into a dict.

    An explicit ``config`` file, else ``pixie.yaml`` under ``baseconfig``
    (default ``./config``). ``conf['templates']`` becomes a list of
    :class:`Path` roots (see :func:`template_roots`).
    """
    try:
        import yaml  # noqa: F401 - anycfg's YAML format needs it
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise PixieConfigError(
            "reading a netboot config file requires the 'config' extra: "
            "pip install netboot[config]"
        ) from exc
    import anycfg

    cwd = LocalPath(_os.getcwd())

    if config:
        path = parse_path(config)
        # Load the file by name with its own directory as the include base_dir.
        # (Using .name avoids relative_to raising for absolute/URI paths.)
        base: Path = path.parent
        configs = [path.name]
    else:
        base = parse_path(baseconfig) if baseconfig else (cwd / "config")
        configs = ["pixie.yaml"]

    loader = anycfg.Loader(
        base_dir=base,
        interpolate=True,
        recursive=True,
        merge="deep",
        # Untrusted on purpose. A netboot config is assembled from `!include`s
        # that often come from inventory exports rather than from the
        # operator's own hand: no command sources, no template files, and
        # `{{ ... }}` in values rendered in the sandbox, so a value cannot
        # reach out of the template language. Where includes may read from is
        # a separate policy: `ANYCFG_CONFINE_TO` confines them.
        policy=anycfg.Policy.untrusted(),
        # `{{ env.NAME }}` reads the environment, so a secret need not be
        # written into the file itself.
        inject_env=True,
    )
    try:
        conf = loader.load(*configs)
    except anycfg.ParseError as exc:  # names the file, line and column
        raise PixieConfigError(f"could not parse the configuration: {exc}") from exc
    except anycfg.PolicyError:
        raise  # a PermissionError: reported as it is
    except anycfg.ConfigError as exc:
        raise PixieConfigError(f"could not load the configuration: {exc}") from exc

    # An empty or comment-only file loads as None, and a file whose top level
    # is a list or a scalar is a config mistake: say so here instead of
    # failing later with an AttributeError from inside the engine.
    if conf is None:
        conf = {}
    if not isinstance(conf, dict):
        raise PixieConfigError(
            f"config must be a mapping at the top level, got "
            f"{type(conf).__name__}: {base}/{configs[0]}"
        )
    for key in conf:
        if not isinstance(key, str):
            raise PixieConfigError(
                f"config key {key!r} is not text ({type(key).__name__}): "
                f"{base}/{configs[0]}"
            )

    conf["templates"] = template_roots(conf.get("templates"), base)
    return conf


def template_roots(declared: object, baseconfig: Path) -> "list[Path]":
    """The template roots a config names, else `<config dir>/templates`.

    A relative root is resolved against the config's directory, as `!include`
    is, so a config means the same thing whichever directory pixie runs from.
    """
    if not declared:
        return [baseconfig / "templates"]
    if not isinstance(declared, list):
        # `templates: some/dir` is the obvious way to write a single path.
        declared = [declared]
    roots = []
    for root in declared:
        path = root if isinstance(root, Path) else parse_path(root)
        if not path.is_absolute():
            path = baseconfig / path
        roots.append(path)
    return roots
