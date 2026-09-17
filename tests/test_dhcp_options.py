"""The DHCP option model: what the query string carries, and what it refuses.

Two vocabularies share a backend's query string — its own connection settings
and the client options — so the tests here are mostly about keeping them apart,
and about refusing anything that varies per target (a boot file pinned to a
connection would hand every target on that server the same one).
"""

import logging
import sys

import pytest

import netboot
from netboot.dhcp import DhcpServer
from netboot.dhcp.options import (
    APPLY_TIME,
    GENERIC_OPTIONS,
    DhcpOptions,
    build_options,
    is_option,
    split_query,
)


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


def test_the_base_class_still_imports_from_its_old_home():
    from netboot.dhcp import DhcpServer, DhcpZone

    assert DhcpServer.__module__ == "netboot.dhcp"
    assert DhcpZone.__module__ == "netboot.dhcp"


class _Fake(DhcpServer):
    """A backend with settings, for splitting tests."""

    SETTINGS = frozenset({"hostsfile", "reload"})


def test_settings_and_options_split_by_vocabulary():
    settings, options, builder = split_query(
        "fake://h?hostsfile=/etc/x&reload=systemctl+reload+d&router=10.0.0.1",
        _Fake.SETTINGS,
        "fake",
    )
    assert settings == {"hostsfile": "/etc/x", "reload": "systemctl reload d"}
    assert options == {"router": "10.0.0.1"}
    assert builder is None


def test_a_repeated_key_is_a_list():
    _, options, _ = split_query(
        "fake://h?domain-name-servers=10.0.0.53&domain-name-servers=10.0.0.54",
        _Fake.SETTINGS,
        "fake",
    )
    assert options["domain-name-servers"] == ["10.0.0.53", "10.0.0.54"]


def test_raw_is_kept_per_backend_and_never_translated():
    _, options, _ = split_query(
        "fake://h?raw.dnsmasq=dhcp-option%3Dtag%3Ax%2C66%2C10.0.0.2",
        _Fake.SETTINGS,
        "fake",
    )
    assert options.raw_for("dnsmasq") == ["dhcp-option=tag:x,66,10.0.0.2"]
    assert options.raw_for("kea") == []
    assert dict(options) == {}


def test_an_unknown_key_names_both_vocabularies():
    with pytest.raises(netboot.PixieConfigError) as excinfo:
        split_query("fake://h?nosuchthing=1", _Fake.SETTINGS, "fake")
    message = str(excinfo.value)
    assert "nosuchthing" in message and "hostsfile" in message and "router" in message


@pytest.mark.parametrize("key", sorted(APPLY_TIME))
def test_an_apply_time_key_is_refused_and_says_where_it_belongs(key):
    with pytest.raises(netboot.PixieConfigError) as excinfo:
        split_query(f"fake://h?{key}=x", _Fake.SETTINGS, "fake")
    message = str(excinfo.value)
    assert key in message
    assert APPLY_TIME[key].split()[1] in message  # "zone" / "image" / "target"


def test_numeric_options_are_always_accepted():
    assert is_option("option-66") and is_option("66")
    assert not is_option("option-abc")
    _, options, _ = split_query("fake://h?option-66=10.0.0.2", _Fake.SETTINGS, "fake")
    assert options["option-66"] == "10.0.0.2"


def test_shipped_backend_settings_never_collide_with_option_names():
    # A backend that names a setting `server-identifier` would silently eat that
    # option; this is the test that stops it, before an operator's boot does.
    from netboot.dhcp import _SHIPPED, _load_backend

    for scheme in _SHIPPED:
        _load_backend(scheme)
        backend = next(
            (c for c in DhcpServer.__subclasses__() if c.__name__ == scheme), None
        )
        if backend is None:  # not implemented yet
            continue
        overlap = set(backend.SETTINGS) & (GENERIC_OPTIONS | set(APPLY_TIME))
        assert not overlap, f"{scheme} settings collide with options: {overlap}"


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


def test_an_unknown_scheme_names_the_extra_for_a_shipped_backend(caplog):
    with caplog.at_level(logging.DEBUG, logger="netboot"):
        with pytest.raises(ValueError, match=r"netboot\[kea\]"):
            DhcpServer("kea://10.0.0.1:8000")


def test_dhcp_options_copy_keeps_raw():
    options = DhcpOptions({"router": "10.0.0.1"}, raw={"dnsmasq": ["x"]})
    clone = options.copy()
    clone["router"] = "10.0.0.2"
    assert options["router"] == "10.0.0.1"
    assert clone.raw_for("dnsmasq") == ["x"]
