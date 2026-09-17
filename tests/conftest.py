"""Shared fixtures.

Two things every test in this suite needs: an environment that does not leak
in from the developer's shell, and a `DhcpServer` registry that does not leak
out of one test into the next (dispatch is by class name, so a leftover
subclass silently shadows another test's backend of the same scheme).
"""

import gc

import pytest

from netboot.dhcp import DhcpServer

#: `PIXIE_*` variables configure the CLI through duho's `Env`, so an ambient
#: one (a developer's `PIXIE_CMDS_PATH`, say) would change what the CLI tests
#: discover.
_PIXIE_ENV_PREFIX = "PIXIE_"


@pytest.fixture(autouse=True)
def isolate_pixie_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(_PIXIE_ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def dhcp_backend():
    """Define a `DhcpServer` subclass for a scheme and unregister it after.

    `DhcpServer(uri)` resolves a scheme by scanning `__subclasses__()`, which
    outlives the test that defined the class.
    """
    created = []

    def make(scheme: str, **namespace):
        cls = type(scheme, (DhcpServer,), namespace)
        created.append(cls)
        return cls

    yield make

    # `__subclasses__()` returns a fresh list, so removing from it changes
    # nothing (which is why the old in-test cleanup was a no-op). The registry
    # holds weak references: dropping the last strong one and collecting is
    # what actually unregisters the class.
    created.clear()
    gc.collect()
