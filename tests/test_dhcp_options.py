"""How netboot layers a target's DHCP options before a server sees them.

Zone defaults, then the image's and the target's `dhcp_options`, then the
`BuildDhcpOptions` hook -- later wins. A server URI holds none of them.
"""

import sys

import dhcpctl
import pytest

import netboot
from netboot.dhcp import NETBOOT_CODES
from netboot.dhcp import build_lease, build_options


def _opt(options, name):
    """The plain value of option `name` in `options`, or None."""
    return dict(dhcpctl.plain_items(options)).get(NETBOOT_CODES.resolve(name))


def test_importing_the_dhcp_package_pulls_in_no_backend_dependency():
    # The whole point of resolving a backend lazily: a config that names no
    # kea:// must not make `import netboot` need requests. Checked in a fresh
    # interpreter, because other tests in this suite import those modules for
    # their own reasons and this process's sys.modules proves nothing.
    import subprocess

    probe = (
        "import sys, netboot.dhcp;"
        "loaded=[m for m in ('requests','paramiko','winrm','pypureomapi')"
        " if m in sys.modules];"
        "print(','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"netboot.dhcp imported {result.stdout.strip()}"


class _Fake(dhcpctl.DHCPServer):
    """A backend with settings, for layering tests."""

    SETTINGS = frozenset({"hostsfile", "reload"})


def _engine(**overrides):
    config = {
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {
            "lan": {
                "network": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "domain": "example.com",
                "nameservers": ["10.0.0.53"],
                "dhcpservers": [],
            }
        },
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
    }
    config.update(overrides)
    return netboot.Pixie(**config)


def _context(engine):
    return engine.make_context(engine.lookup_target("host1"))


def test_zone_defaults_come_from_the_zone():
    options = build_options(_context(_engine()), _Fake("fake://h"))
    assert _opt(options, "router") == ["10.0.0.1"]
    assert _opt(options, "domain-name-servers") == ["10.0.0.53"]
    assert _opt(options, "domain-name") == "example.com"
    assert str(_opt(options, "subnet-mask")) == "255.255.255.0"


def test_the_merge_order_is_zone_then_image_then_target():
    engine = _engine(
        images={
            "debian": {"template_path": [], "dhcp_options": {"router": "10.0.0.8"}}
        },
        targets={
            "host1": {
                "hostname": "host1",
                "ip": "10.0.0.5",
                "image": "debian",
                "dhcp_options": {"router": "10.0.0.9"},
            }
        },
    )
    # zone says .1, the image .8, the target .9
    assert _opt(build_options(_context(engine), _Fake("fake://h")), "router") == [
        "10.0.0.9"
    ]
    engine_no_target = _engine(
        images={"debian": {"template_path": [], "dhcp_options": {"router": "10.0.0.8"}}}
    )
    options = build_options(_context(engine_no_target), _Fake("fake://h"))
    assert _opt(options, "router") == ["10.0.0.8"]


def test_an_unknown_option_on_an_image_is_named():
    engine = _engine(
        images={"debian": {"template_path": [], "dhcp_options": {"nosuch": 1}}}
    )
    with pytest.raises(netboot.PixieConfigError, match="nosuch"):
        build_options(_context(engine), _Fake("fake://h"))


@pytest.mark.parametrize("value", ["0a0b", "0a:0b", {"hex": "0a0b"}])
def test_an_option_with_no_type_takes_hex(value):
    engine = _engine(
        images={"debian": {"template_path": [], "dhcp_options": {"option-224": value}}}
    )
    assert (
        _opt(build_options(_context(engine), _Fake("fake://h")), "option-224") == "0a0b"
    )


def _hooked(hook):
    return netboot.Pixie(
        hooks=[hook],
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )


def _on_options(result):
    def hook(event, engine, value, kwargs):
        if event is netboot.PixieEvent.BuildDhcpOptions:
            return result(value)
        return value

    return hook


def test_a_hook_that_returns_nothing_is_reported():
    engine = _hooked(_on_options(lambda options: None))
    with pytest.raises(netboot.PixieConfigError, match="BuildDhcpOptions"):
        build_options(_context(engine), _Fake("fake://h"))


def test_a_hook_may_return_a_mapping_of_option_names():
    engine = _hooked(_on_options(lambda options: {"router": "10.0.0.99"}))
    options = build_options(_context(engine), _Fake("fake://h"))
    assert dict(dhcpctl.plain_items(options)) == {3: ["10.0.0.99"]}


def test_a_hook_value_its_option_cannot_hold_is_a_config_error():
    engine = _hooked(_on_options(lambda options: {"router": "gw"}))
    with pytest.raises(netboot.PixieConfigError, match="BuildDhcpOptions.*ROUTER"):
        build_options(_context(engine), _Fake("fake://h"))


def test_the_hook_sees_the_options_and_can_change_them():
    seen = {}

    def hook(event, engine, value, kwargs):
        if event is netboot.PixieEvent.BuildDhcpOptions:
            seen["target"] = kwargs["target"]._id
            value[NETBOOT_CODES.resolve("ntp-servers")] = "10.0.0.123"
        return value

    engine = netboot.Pixie(
        hooks=[hook],
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )
    options = build_options(_context(engine), _Fake("fake://h"))
    assert seen["target"] == "host1"
    assert _opt(options, "ntp-servers") == ["10.0.0.123"]


def test_the_names_netboot_configs_always_used_still_work():
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "a.efi", "lease-time": 600},
            }
        }
    )
    lease = build_lease(_context(engine), _Fake("fake://h"))
    # boot-file-name has always meant the boot file a ROM loads: `file`.
    assert lease.file == "a.efi" and _opt(lease.options, "bootfile-name") is None
    assert _opt(lease.options, 51) == 600


def test_next_server_is_the_leases_header_field_not_an_option():
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"next-server": "10.0.0.2", "router": "10.0.0.1"},
            }
        },
        targets={
            "host1": {
                "hostname": "host1",
                "ip": "10.0.0.5",
                "image": "debian",
                "dhcp_options": {"next-server": "10.0.0.3"},
            }
        },
    )
    ctx = _context(engine)
    options = build_options(ctx, _Fake("fake://h"))
    # Every key is an option code; next-server is none of them.
    assert all(isinstance(code, int) for code in options)
    assert _opt(options, "router") == ["10.0.0.1"]
    lease = build_lease(ctx, _Fake("fake://h"))
    assert str(lease.siaddr) == "10.0.0.3", "the target's wins"
    assert str(lease.ip) == "10.0.0.5" and lease.options == options


def test_a_next_server_that_is_no_address_names_the_image():
    engine = _engine(
        images={
            "debian": {"template_path": [], "dhcp_options": {"next-server": "tftp"}}
        }
    )
    with pytest.raises(netboot.PixieConfigError, match="'debian'.*siaddr"):
        build_options(_context(engine), _Fake("fake://h"))


def test_a_value_an_option_cannot_hold_names_the_image():
    engine = _engine(
        images={"debian": {"template_path": [], "dhcp_options": {"router": "gw"}}}
    )
    with pytest.raises(netboot.PixieConfigError, match="'debian'.*ROUTER"):
        build_options(_context(engine), _Fake("fake://h"))


def test_a_config_declares_its_own_option_names():
    engine = _engine(
        dhcp={
            "options": {
                "site-tag": {"code": 224, "type": "String"},
                "gw": "router",
                "tag": "site-tag",
            }
        },
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"tag": "rack-7", "gw": "10.0.0.9"},
            }
        },
    )
    options = build_options(_context(engine), _Fake("fake://h"))
    # The declared type is the config's codemap's, and the bag decodes with it.
    assert options.get(224) == "rack-7"
    assert _opt(options, 3) == ["10.0.0.9"]
    assert dhcpctl.DHCPOptionCode(224).get_type().__name__ == "Bytes"


def test_a_bad_dhcp_section_is_a_config_error():
    with pytest.raises(netboot.PixieConfigError, match="dhcp.options"):
        _engine(dhcp={"options": {"x": {"code": 999}}})


def test_a_zone_server_uri_takes_no_options(tmp_path):
    hosts = (tmp_path / "hosts").as_posix() + "/"
    with pytest.raises(netboot.PixieConfigError, match="options belong to the"):
        _engine(
            dhcpzones={
                "lan": {
                    "network": "10.0.0.0/24",
                    "dhcpservers": [f"dnsmasq:///?hostsfile={hosts}&router=10.0.0.7"],
                }
            },
        )
