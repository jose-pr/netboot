"""Shared fixtures.

Two things every test in this suite needs: an environment that does not leak
in from the developer's shell, and a dhcpctl backend registry that does not
leak out of one test into the next.
"""

import dhcpctl
import pytest

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
    """Register a `dhcpctl.DHCPServer` subclass for a scheme, unregistered after."""
    created = []

    def make(scheme: str, **namespace):
        cls = type(scheme, (dhcpctl.DHCPServer,), namespace)
        dhcpctl.register_backend(scheme, cls)
        created.append(scheme)
        return cls

    yield make

    for scheme in created:
        dhcpctl.unregister_backend(scheme)
