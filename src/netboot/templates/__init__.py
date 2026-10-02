from typing import TYPE_CHECKING, Any, Callable, MutableMapping, Tuple, Type, Union

from jinja2 import TemplateNotFound
from pathlib_next import Path, PosixPathname

from ..utils.misc import parse_path
from .common import (
    ANY_SUFFIX,
    DEFAULT_PRIORITY,
    FALLBACK_PRIORITY,
    JINJA_UNDEFINED,
    TEMPLATE_TYPES,
    UNDEFINED_MODES,
    Renderer,
    Template,
    TemplateEngineError,
    by_priority,
    register_template_type,
    template_extensions,
    unregister_template_type,
)
from .copy import CopyTemplate
from .jinja import JinjaTemplate, _Jinja2Template
from .shell import ShellTemplate

# The shipped engines, in the order they are consulted when priorities tie.
# `CopyTemplate` carries `FALLBACK_PRIORITY`, so it is last whatever a plugin
# registers afterwards.
for _engine in (JinjaTemplate, ShellTemplate, CopyTemplate):
    register_template_type(_engine)
del _engine

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

    `template_types` defaults to every **registered** engine
    (`register_template_type`), so a plugin loaded with `--load-module` is
    picked up without the caller rebuilding the list. Either way the engines are
    ordered by `PRIORITY`, highest first, which is what keeps the catch-all
    `CopyTemplate` behind an engine that claims a suffix.
    """

    def __init__(
        self,
        searchpaths: list,
        template_types: Union[list, Tuple, None] = None,
        undefined: str = "strict",
    ) -> None:
        self.searchpaths = [
            path if isinstance(path, Path) else parse_path(path) for path in searchpaths
        ]
        #: Resolved once, at construction: a Loader built for one run must not
        #: change engines underneath it if something imports a plugin later.
        self.template_types = by_priority(
            TEMPLATE_TYPES if template_types is None else template_types
        )
        #: Passed to every non-Jinja template built here; the Jinja engine gets
        #: its own class through the `Renderer`.
        self.undefined = undefined
        super().__init__()

    def get_source(
        self, environment: Renderer, template: str, **options
    ) -> Tuple[str, str, Callable[[], bool]]:
        """Find `template` and return its text, path and freshness check.

        Jinja2's loader contract is text, so this decodes. `load` uses
        `find_source` instead: which engine gets the file decides whether it is
        decoded at all.
        """
        data, filename, uptodate = self.find_source(environment, template, **options)
        # Templates are UTF-8, not whatever the machine's locale says: the same
        # tree must render identically on every host.
        return data.decode("utf-8"), filename, uptodate

    def find_source(
        self, environment: Renderer, template: str, **options
    ) -> Tuple[bytes, str, Callable[[], bool]]:
        """Find `template` and return its **bytes**, path and freshness check."""
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

                # Read as bytes: a copy template must be byte-exact, and only
                # the engine that claims the file knows whether decoding it is
                # even meaningful.
                contents = path.read_bytes()
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
        data, filename, uptodate = self.find_source(environment, name)
        # Decoding is deferred: a `BINARY` engine (the copy engine) takes the
        # bytes as they are, and a file that is not UTF-8 text at all must still
        # reach it instead of failing on the way.
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError as error:
            source, decode_error = None, error
        else:
            decode_error = None
        for t in self.template_types:
            if t.can_process(
                PosixPathname(filename), data if source is None else source
            ):
                if getattr(t, "BINARY", False):
                    template = t(data)
                    template._globals_ = globals
                    template._uptodate_ = uptodate
                    template._undefined_ = self.undefined
                    template.loader = self
                    return template
                if decode_error is not None:
                    raise TemplateEngineError(
                        f"{filename} is not valid UTF-8 text, which "
                        f"{t.__name__} needs; only a BINARY engine (the copy "
                        f"engine) can take it as bytes"
                    ) from decode_error
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
                    template._undefined_ = self.undefined

                template.loader = self
                return template
        # Only reachable when `template_types` was narrowed and carries no
        # catch-all: the default list ends in `CopyTemplate`, which copies
        # an unclaimed file as it is.
        claimed = []
        for t in self.template_types:
            suffixes = template_extensions(t)
            claimed.append(
                f"{t.__name__} ({', '.join(suffixes) if suffixes else 'any suffix'})"
            )
        raise TemplateEngineError(
            f"no template engine handles {name!r} (resolved to {filename}); "
            f"engines: {'; '.join(claimed) or 'none configured'}"
        )
