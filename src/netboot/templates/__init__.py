from typing import TYPE_CHECKING, Any, Callable, MutableMapping, Tuple, Type, Union

from jinja2 import TemplateNotFound
from pathlib_next import Path, PosixPathname

from ..utils.misc import parse_path
from .common import Renderer, Template
from .jinja import JinjaTemplate, _Jinja2Template
from .shell import ShellTemplate

if TYPE_CHECKING:
    from . import Template
    from .. import PixieContext

from jinja2.loaders import BaseLoader as _JinjaLoader

#: Where a relative template path is looked for when the config names no
#: template root of its own.
DEFAULT_TEMPLATE_DIR = "templates"


def _is_anchored(path: Path) -> bool:
    """Does this path carry its own root (so no template dir applies)?

    A URI is anchored by its authority; a local path by a drive or a leading
    separator. `LocalPath("/srv/tftp").is_absolute()` is False on Windows -- no
    drive -- but a rooted path is still not something to look for *inside* a
    template directory.
    """
    if getattr(path, "source", None):
        return True
    try:
        if path.is_absolute():
            return True
    except (AttributeError, NotImplementedError):  # pragma: no cover
        return True
    return str(path).startswith(("/", "\\"))


class Loader(_JinjaLoader):
    """Resolves template names against the configured template roots.

    `searchpaths` are the roots (`config["templates"]`, `./templates` by
    default). A *relative* per-target or per-image `template_path` is resolved
    inside each root rather than against the process's working directory, so a
    config means the same thing whichever directory `pixie` was run from.
    """

    def __init__(
        self,
        searchpaths: list,
        template_types: list[Type[Template]] = [JinjaTemplate, ShellTemplate],
    ) -> None:
        self.searchpaths = [
            path if isinstance(path, Path) else parse_path(path) for path in searchpaths
        ]
        self.template_types = template_types
        super().__init__()

    def get_source(
        self, environment: Renderer, template: str, **options
    ) -> Tuple[str, str, Callable[[], bool]]:
        """Find `template` and return its text, path and freshness check."""
        ctx: "PixieContext" = environment.globals.get("ctx")
        options: dict[str, str]

        if ":" in template:
            _options, filename = template.rsplit(":", maxsplit=1)
            for opt in _options.split(";"):
                if "=" in opt:
                    k, v = opt.split("=", maxsplit=1)
                    options[k] = v
                else:
                    options[opt] = True
        else:
            filename = template

        filename = filename.removeprefix("/")
        if ".." in PosixPathname(filename).parts:
            # A template name is resolved against every search root, so a `..`
            # segment would read files outside them. Jinja's own loaders refuse
            # this for the same reason.
            raise TemplateNotFound(
                template,
                message=f"template name escapes the search paths: {template!r}",
            )
        _filename = PosixPathname(filename)
        _parent = _filename.parent
        if _parent != _filename and _parent.as_posix() != ".":
            parent = _parent.as_posix()
            filename = _filename.name
        else:
            parent = None
        # A `http://...` entry in `templates` or in an image's
        # `template_path` is a URI, not a directory named "http:" under the
        # CWD -- which is what `Path(".") / str(path)` used to make of it.
        roots: list[Path] = self.searchpaths or [parse_path(DEFAULT_TEMPLATE_DIR)]
        searchpaths: list[Path] = []
        for path in (ctx.searchpaths if ctx else None) or []:
            path = path if isinstance(path, Path) else parse_path(str(path))
            if _is_anchored(path):
                searchpaths.append(path)
            else:
                # `template_path: [debian]` means `<template root>/debian`,
                # for every configured root.
                searchpaths.extend(root / str(path) for root in roots)
        searchpaths.extend(roots)

        # Without a context there is no target to name candidates after, but a
        # plain name must still resolve: `Loader` is usable on its own.
        candidates = (
            ctx._template_names(filename, **options) if ctx is not None else [filename]
        )
        for filename in candidates:
            for searchpath in searchpaths:
                if parent is not None:
                    searchpath = searchpath / parent
                if not searchpath.exists():
                    continue
                # An exact filename always wins; among stem matches
                # (`boot` -> `boot.j2`, `boot.sh`, `boot.j2.bak`) take the
                # first by name, so the result does not depend on the order
                # the filesystem happens to hand back.
                path = None
                for entry in searchpath.iterdir():
                    if entry.name == filename:
                        path = entry
                        break
                    if entry.stem == filename and (
                        path is None or entry.name < path.name
                    ):
                        path = entry
                if path is None:
                    continue

                # Templates are UTF-8, not whatever the machine's locale says:
                # the same tree must render identically on every host.
                contents = path.read_text(encoding="utf-8")
                mtime = path.stat().st_mtime

                def uptodate(path=path, mtime=mtime) -> bool:
                    try:
                        return path.stat().st_mtime == mtime
                    except OSError:
                        return False

                try:
                    fspath = path.__fspath__()
                except NotImplementedError:
                    fspath = "/".join(path.segments)
                return contents, fspath, uptodate
        raise TemplateNotFound(template)

    def load(
        self,
        environment: Renderer,
        name: str,
        globals: Union[MutableMapping[str, Any], None] = None,
    ) -> Template:
        """Build the template object, picking the first engine that can process it."""
        if globals is None:
            globals = {}
        source, filename, uptodate = self.get_source(environment, name)
        for t in self.template_types:
            if t.can_process(PosixPathname(filename), source):
                if issubclass(t, _Jinja2Template):
                    code = None
                    # try to load the code from the bytecode cache if there is a
                    # bytecode cache configured.
                    bcc = environment.bytecode_cache
                    if bcc is not None:
                        bucket = bcc.get_bucket(environment, name, filename, source)
                        code = bucket.code

                    # if we don't have code so far (not cached, no longer up to
                    # date) etc. we compile the template
                    if code is None:
                        code = environment.compile(source, name, filename)

                    # if the bytecode cache is available and the bucket doesn't
                    # have a code so far, we give the bucket the new code and put
                    # it back to the bytecode cache.
                    if bcc is not None and bucket.code is None:
                        bucket.code = code
                        bcc.set_bucket(bucket)

                    template = t.from_code(environment, code, globals, uptodate)
                else:
                    template = t(source)
                    template._globals_ = globals
                    template._uptodate_ = uptodate

                template.loader = self
                return template
        raise Exception(f"No engine available for template:{name}")
