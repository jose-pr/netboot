"""Backend-native fragments, as netboot hands them to a server.

The worked example is the iPXE chainload: a PXE ROM must be handed the iPXE
binary, and iPXE itself must then be handed the script, or it loads itself
forever. A fragment comes from a backend subclass that decides per target,
from the context netboot hands it; how each backend emits one is dhcpctl's,
and tested there.
"""

import dhcpctl
import pytest

import netboot

IPXE_DHCPD = (
    'if exists user-class and option user-class = "iPXE" '
    '{ filename "boot.ipxe"; } else { filename "undionly.kpxe"; }'
)


def _engine(servers):
    return netboot.Pixie(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "undionly.kpxe"},
            }
        },
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "dhcpservers": list(servers),
            }
        },
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    )


@pytest.fixture
def ipxe():
    class ipxe(dhcpctl.DHCPServer):
        applied: list = []

        def extras(self, reservation, phase="add"):
            lines = super().extras(reservation, phase)
            # The reservation's context is netboot's, which is what lets a
            # subclass look at the image -- config cannot.
            ctx = reservation.context
            if phase == "add" and getattr(ctx.image, "_id", "") == "debian":
                lines.append(IPXE_DHCPD)
            return lines

        def add(self, reservation):
            ipxe.applied = self.extras(reservation, "add")

    dhcpctl.register_backend("ipxe", ipxe)
    yield ipxe
    dhcpctl.unregister_backend("ipxe")


def test_a_subclass_can_decide_per_target(ipxe):
    engine = _engine(["ipxe://host/"])
    engine.initialize(engine.lookup_target("web01"))
    assert ipxe.applied == [IPXE_DHCPD]


def test_a_server_uri_takes_no_backend_native_text():
    with pytest.raises(netboot.PixieConfigError, match="unknown key 'raw.dhcpd'"):
        _engine(["dhcpd://host/?raw.dhcpd=log(1);"])
