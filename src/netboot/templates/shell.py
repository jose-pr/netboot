from string import Template as _BasicTemplate

from pathlib_next import Path

from ..logging import LOGGER
from ..utils import flatten
from .common import Template


class _Basic(_BasicTemplate):
    delimiter = "%"
    #: Braced form only. `string.Template` would also accept a bare `%name`
    #: and, worse, treat an unknown one as *invalid* and raise -- which made
    #: every kickstart file (`%packages`, `%pre`, `%post`) unrenderable. The
    #: `named` and `invalid` groups are kept (the class requires them) but can
    #: never match, so a bare `%` is ordinary text.
    pattern = r"""
    %(?:
      (?P<escaped>%)                        |
      (?P<named>(?!))                       |
      {(?P<braced>[_a-z][_a-z0-9]*)}        |
      (?P<invalid>(?!))
    )
    """


class ShellTemplate(Template):
    def __init__(self, template: str) -> None:
        self._template = _Basic(template)

    def render(self, **extras):
        _globals = getattr(self, "_globals_", {})
        context = _globals.get("ctx", {})
        _args = flatten(context)
        _upper = {}
        for k, v in _args.items():
            if v is None:
                v = ""
            elif isinstance(v, bool):
                v = str(v).lower()
            else:
                v = str(v)
            key = k.upper()
            if key in _upper and _upper[key] != v:
                # `domain` and `DOMAIN` are different context keys but one
                # placeholder; say which value the template will get.
                LOGGER.warning(
                    "shell template variable %s has two sources (%r and %r); "
                    "using the later",
                    key,
                    _upper[key],
                    v,
                )
            _upper[key] = v
        return self._template.substitute(_upper)

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        return True
