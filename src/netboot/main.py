"""The `pixie` command line's former home; `netboot.cli` is where it lives.

Kept so `netboot.main:main` and the names imported from here keep working.
"""

from .cli import main
from .cli._config import load_config, template_roots
from .cli._root import (  # noqa: F401 - the names tests and plugins import
    _ERROR_STATUS,
    _USER_ERRORS,
    Pixie_,
    PixieArgs,
    _discover,
    _dispatch,
    _report_error,
    _wants_netboot,
)
from .utils.misc import parse_path


def _load_config(args: object) -> dict:
    """`load_config` from an object carrying `config` and `baseconfig`."""
    return load_config(getattr(args, "config", None), getattr(args, "baseconfig", None))


_template_roots = template_roots

__all__ = ["main", "PixieArgs", "Pixie_", "parse_path"]

if __name__ == "__main__":
    raise SystemExit(main())
