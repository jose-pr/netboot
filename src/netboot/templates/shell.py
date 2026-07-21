from string import Template as _BasicTemplate

from ..utils import flatten
from .common import *


class _Basic(_BasicTemplate):
    delimiter = "%"


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
            _upper[k.upper()] = v
        return self._template.substitute(_upper)

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        return True
