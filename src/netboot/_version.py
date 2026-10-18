"""The installed distribution's version.

Its own module so `netboot.engine` can read it without importing the package
root, which imports `netboot.engine`.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("netboot")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"
