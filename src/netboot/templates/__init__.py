from typing import TYPE_CHECKING, Any, Callable, MutableMapping, Tuple, Type, Union

from jinja2 import TemplateNotFound
from pathlib_next import Path, PosixPathname

from .common import Renderer, Template
from .jinja import JinjaTemplate, _Jinja2Template
from .shell import ShellTemplate

if TYPE_CHECKING:
    from . import Template
    from .. import PixieContext

from jinja2.loaders import BaseLoader as _JinjaLoader


class Loader(_JinjaLoader):
    def __init__(
        self,
        searchpaths: list,
        template_types: list[Type[Template]] = [JinjaTemplate, ShellTemplate],
    ) -> None:
        self.searchpaths = [
            path if isinstance(path, Path) else Path(path) for path in searchpaths
        ]
        self.template_types = template_types
        super().__init__()

    def get_source(
        self, environment: Renderer, template: str, **options
    ) -> Tuple[str, str, Callable[[], bool]]:
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
        _filename = PosixPathname(filename)
        _parent = _filename.parent
        if _parent != _filename and _parent.as_posix() != ".":
            parent = _parent.as_posix()
            filename = _filename.name
        else:
            parent = None
        searchpaths: list[Path] = [
            path if isinstance(path, Path) else (Path(".") / str(path))
            for path in [*(ctx.searchpaths or []), *self.searchpaths]
        ]

        for filename in ctx._template_names(filename, **options):
            for searchpath in searchpaths:
                if parent is not None:
                    searchpath = searchpath / parent
                if not searchpath.exists():
                    continue
                for path in searchpath.iterdir():
                    if path.name == filename or path.stem == filename:
                        contents = path.read_text()
                        mtime = path.stat().st_mtime

                        def uptodate() -> bool:
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
                    setattr(template, "is_up_to_date", uptodate)

                template.loader = self
                return template
        raise Exception(f"No engine available for template:{name}")
