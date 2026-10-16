"""How netboot layers a target's DHCP options before a server sees them.

Zone defaults, then the server's connection query, then the image's and the
target's `dhcp_options`, then the server's `options_builder`, then the
`BuildDhcpOptions` hook -- later wins. What the query string itself accepts is
dhcpctl's, and tested there.
"""

import sys

import dhcpctl
import pytest

import netboot
from netboot.dhcp import build_options


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
    assert str(options["router"]) == "10.0.0.1"
    assert [str(n) for n in options["domain-name-servers"]] == ["10.0.0.53"]
    assert options["domain-name"] == "example.com"
    assert str(options["subnet-mask"]) == "255.255.255.0"


def test_the_merge_order_is_zone_then_connection_then_image_then_target():
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
    # zone says .1, the connection says .7, the image .8, the target .9
    options = build_options(_context(engine), _Fake("fake://h?router=10.0.0.7"))
    assert options["router"] == "10.0.0.9"

    engine_no_target = _engine(
        images={"debian": {"template_path": [], "dhcp_options": {"router": "10.0.0.8"}}}
    )
    assert (
        build_options(_context(engine_no_target), _Fake("fake://h?router=10.0.0.7"))[
            "router"
        ]
        == "10.0.0.8"
    )


def test_an_unknown_option_on_an_image_is_named():
    engine = _engine(
        images={"debian": {"template_path": [], "dhcp_options": {"nosuch": 1}}}
    )
    with pytest.raises(netboot.PixieConfigError, match="nosuch"):
        build_options(_context(engine), _Fake("fake://h"))


def test_the_options_builder_runs_last_of_the_config_layers():
    calls = []

    def builder(ctx, options):
        calls.append(ctx.target._id)
        options["boot-file-name"] = "from-builder"
        return options

    server = _Fake("fake://h")
    server.options_builder = builder
    options = build_options(_context(_engine()), server)
    assert calls == ["host1"]
    assert options["boot-file-name"] == "from-builder"


def test_a_builder_that_returns_nothing_is_reported():
    server = _Fake("fake://h")
    server.options_builder = lambda ctx, options: None
    with pytest.raises(netboot.PixieConfigError, match="options_builder"):
        build_options(_context(_engine()), server)


def test_a_builder_returning_a_plain_dict_keeps_the_raw_section():
    server = _Fake("fake://h?raw.dnsmasq=verbatim")
    server.options_builder = lambda ctx, options: {"router": "10.0.0.99"}
    options = build_options(_context(_engine()), server)
    assert options["router"] == "10.0.0.99"
    assert options.raw_for("dnsmasq") == ["verbatim"]


def test_the_hook_sees_the_options_and_can_change_them():
    seen = {}

    def hook(event, engine, value, kwargs):
        if event is netboot.PixieEvent.BuildDhcpOptions:
            seen["target"] = kwargs["target"]._id
            value["ntp-servers"] = "10.0.0.123"
        return value

    engine = netboot.Pixie(
        hooks=[hook],
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )
    options = build_options(_context(engine), _Fake("fake://h"))
    assert seen["target"] == "host1"
    assert options["ntp-servers"] == "10.0.0.123"
