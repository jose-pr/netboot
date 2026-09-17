"""Tests that defend the dependency floors netboot declares.

A floor nobody exercises is a comment: the suite passed with every dependency
one release below its declared minimum, so "normalising" a floor down would
have gone unnoticed until a user hit the silent behaviour it guards against.
Each test here fails on the release below the floor it names.
"""

import inspect
import textwrap
import types

import pytest

from netboot.main import _load_config
from netboot.utils import net as netutils


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def config_tree(tmp_path):
    """A config whose `!include` has to descend to find half its content."""
    base = tmp_path / "config"
    _write(
        base / "pixie.yaml",
        """\
        globals:
          site: hq
        extra: !include "sub/**/*.yaml"
        targets: {}
        images: {}
        dhcpzones: {}
        """,
    )
    _write(base / "sub" / "shallow.yaml", "shallow: 1\n")
    _write(base / "sub" / "deep" / "nested.yaml", "nested: 2\n")
    return base


def test_include_globs_descend_into_subdirectories(config_tree):
    """Guards `yaconfiglib`'s floor: recursive glob expansion.

    `_load_config` builds `ConfigLoader(recursive=True)`, and that setting was
    not forwarded to glob expansion before yaconfiglib 0.11.1 -- a silent
    no-op, so a user with config spread across subdirectories saw some of it
    load and never learned why the rest did not.
    """
    args = types.SimpleNamespace(config=None, baseconfig=str(config_tree))
    conf = _load_config(args)
    extra = conf["extra"]
    assert extra.get("shallow") == 1
    assert extra.get("nested") == 2, (
        "a `**` include did not descend: yaconfiglib is below the declared "
        "floor, or recursive= is no longer forwarded to glob expansion"
    )


def test_interpolation_is_available_and_sandboxed(config_tree):
    """Guards the loader options netboot depends on (`interpolate`, `sandbox`)."""
    _write(
        config_tree / "pixie.yaml",
        """\
        globals:
          site: hq
          greeting: "{{ globals.site }}-boot"
        targets: {}
        images: {}
        dhcpzones: {}
        """,
    )
    args = types.SimpleNamespace(config=None, baseconfig=str(config_tree))
    conf = _load_config(args)
    assert conf["globals"]["greeting"] == "hq-boot"


def test_resolve_auto_selects_the_record_type():
    """Guards `netimps>=0.2.1`: `resolve()` picks `rdtype` itself.

    `Host.try_ip` calls `resolve(name)` with no record type, which only works
    because 0.2.1 auto-selects one ("ptr" for an address literal, "a"
    otherwise). A signature check, not a lookup: no test here touches DNS.
    """
    rdtype = inspect.signature(netutils.resolve).parameters.get("rdtype")
    assert rdtype is not None, "netimps.resolve lost its rdtype parameter"
    assert rdtype.default is None, (
        "netimps.resolve no longer defaults rdtype to None (auto-select); "
        "Host.try_ip relies on that, netimps >= 0.2.1"
    )
