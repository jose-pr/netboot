"""`dhcp_when:` -- how netboot layers named conditions onto a target.

The worked example is the iPXE chainload: serve the iPXE binary to a PXE ROM and
the script to iPXE, or iPXE loads itself forever. A condition is declared once,
under a name, on a zone, an image or a target; what each backend makes of one is
dhcpctl's, and tested there.
"""

import logging

import dhcpctl
import pytest

import netboot
from netboot.dhcp import build_conditions

IPXE = {
    "ipxe": {
        "match": {"user-class": "iPXE"},
        "options": {"boot-file-name": "boot.ipxe"},
    }
}


def _engine(**overrides):
    config = {
        "images": {
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "undionly.kpxe"},
                "dhcp_when": dict(IPXE),
            }
        },
        "dhcpzones": {"lan": {"network": "10.0.0.0/24", "subnet_id": 1}},
        "targets": {
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    }
    config.update(overrides)
    return netboot.Pixie(**config)


def _ctx(engine=None, target="web01"):
    engine = engine or _engine()
    return engine.make_context(engine.lookup_target(target))


def test_a_condition_is_keyed_by_the_name_backends_will_use():
    conditions = build_conditions(_ctx())
    assert list(conditions) == ["ipxe"]
    assert conditions["ipxe"].name == "ipxe"
    assert conditions["ipxe"].match == {"user-class": "iPXE"}
    assert conditions["ipxe"].option_codes == {77: "iPXE"}


def test_a_target_overrides_an_image_condition_of_the_same_name():
    # Keying by name is what makes this a replacement rather than a second
    # policy that also matches.
    engine = _engine(
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
                "dhcp_when": {
                    "ipxe": {
                        "match": {"user-class": "iPXE"},
                        "options": {"boot-file-name": "special.ipxe"},
                    }
                },
            }
        }
    )
    conditions = build_conditions(_ctx(engine))
    assert list(conditions) == ["ipxe"]
    assert conditions["ipxe"].options["boot-file-name"] == "special.ipxe"


def test_a_zone_condition_and_an_image_condition_both_apply():
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcp_when": {
                    "winpe": {
                        "match": {"vendor-class": "PXEClient:Arch:00000"},
                        "options": {"boot-file-name": "wdsnbp.com"},
                    }
                },
            }
        }
    )
    assert sorted(build_conditions(_ctx(engine))) == ["ipxe", "winpe"]


@pytest.mark.parametrize(
    "entry, message",
    [
        ("not-a-mapping", "must be a mapping"),
        ({"match": {}, "options": {"boot-file-name": "x"}}, "non-empty `match`"),
        ({"match": {"user-class": "iPXE"}}, "non-empty `options`"),
        (
            {"match": {"hostname": "x"}, "options": {"boot-file-name": "y"}},
            "cannot match",
        ),
        (
            {"match": {"user-class": "x"}, "options": {"nonsense": "y"}},
            "unknown dhcp option",
        ),
        (
            {
                "match": {"user-class": "x"},
                "options": {"boot-file-name": "y"},
                "zzz": 1,
            },
            "unknown key",
        ),
    ],
)
def test_a_malformed_condition_names_the_mistake(entry, message):
    engine_args = {
        "images": {"debian": {"template_path": [], "dhcp_when": {"bad": entry}}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    }
    with pytest.raises(netboot.PixieConfigError, match=message):
        build_conditions(_ctx(netboot.Pixie(**engine_args)))


def test_a_name_that_no_backend_could_use_is_refused():
    # The name reaches a PowerShell argument, a dhcpd identifier and a Kea class
    # name; it is kept to the intersection rather than quoted three ways.
    engine = netboot.Pixie(
        images={
            "debian": {
                "template_path": [],
                "dhcp_when": {
                    "bad name": {
                        "match": {"user-class": "x"},
                        "options": {"boot-file-name": "y"},
                    }
                },
            }
        },
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    )
    with pytest.raises(netboot.PixieConfigError, match="letters, digits"):
        build_conditions(_ctx(engine))


# -- arming with a backend that refuses ------------------------------------


def test_one_backend_refusing_still_arms_the_others(caplog, tmp_path, request):
    # Best-effort arming is what makes refusing safe: the zone's other servers
    # are armed and the run succeeds.
    armed = []

    class supports(dhcpctl.DHCPServer):
        def add(self, reservation):
            for condition in reservation.conditions.values():
                armed.append(condition.name)

    dhcpctl.register_backend("supports", supports)
    request.addfinalizer(lambda: dhcpctl.unregister_backend("supports"))

    hosts = tmp_path / "h"
    hosts.mkdir()
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": [
                    f"dnsmasq:///?hostsfile={hosts.as_posix()}",
                    "supports://a",
                ],
            }
        }
    )
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.initialize(engine.lookup_target("web01"))
    assert armed == ["ipxe"], "the supporting backend must still be armed"
    assert "dnsmasq" in caplog.text


def test_a_zone_whose_only_backend_refuses_fails(tmp_path):
    hosts = tmp_path / "h"
    hosts.mkdir()
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": [f"dnsmasq:///?hostsfile={hosts.as_posix()}"],
            }
        }
    )
    with pytest.raises(dhcpctl.ConditionUnsupportedError):
        engine.initialize(engine.lookup_target("web01"))
