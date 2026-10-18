"""Tests for DHCP zone parsing and how a zone builds its servers."""

import types

import dhcpctl
import pytest

import netboot
from netboot.dhcp import DHCPZone, connect, server_from


def test_zone_parses_network_and_nameservers():
    zone = DHCPZone(
        network="10.20.30.0/24",
        gateway="10.20.30.1",
        nameservers="10.20.30.53",
        search="example.com",
    )
    assert str(zone.network.network_address) == "10.20.30.0"
    assert str(zone.gateway) == "10.20.30.1"
    # scalar nameservers/search are normalised to lists
    assert [str(ns) for ns in zone.nameservers] == ["10.20.30.53"]
    assert zone.search == ["example.com"]
    assert zone.nameserver == zone.nameservers[0]


def test_zone_derives_network_from_gateway_cidr():
    zone = DHCPZone(gateway="192.168.5.1/24")
    assert str(zone.network.network_address) == "192.168.5.0"
    assert str(zone.gateway) == "192.168.5.1"


def test_unknown_dhcpserver_scheme_raises():
    with pytest.raises(netboot.PixieConfigError):
        connect("nosuchscheme://host")


def test_connect_dispatches_to_a_registered_backend(dhcp_backend):
    memdhcp = dhcp_backend("memdhcp")
    server = connect("memdhcp://host/path")
    assert isinstance(server, memdhcp)
    assert server.uri == "memdhcp://host/path"
    assert server.owner == "netboot"


def test_zone_builds_dhcpservers_from_uris(dhcp_backend):
    zoneback = dhcp_backend("zoneback")
    zone = DHCPZone(network="10.0.0.0/24", dhcpservers=["zoneback://h"])
    assert len(zone.dhcpservers) == 1
    assert isinstance(zone.dhcpservers[0], zoneback)


def test_zone_keeps_a_server_object_as_it_is(dhcp_backend):
    zoneback = dhcp_backend("zoneback")
    server = dhcpctl.connect("zoneback://h")
    zone = DHCPZone(network="10.0.0.0/24", dhcpservers=[server])
    assert zone.dhcpservers == [server]


@pytest.mark.parametrize(
    "entry, key",
    [
        (
            {"uri": "windhcp://dhcp01/?transport=winrm", "password": "s3cret"},
            "password",
        ),
        ({"uri": "windhcp://dhcp01/?transport=winrm", "user": "s3cret"}, "user"),
        ({"uri": "dhcpd://dhcp01", "omapi_key": "s3cret"}, "omapi_key"),
        ({"uri": "kea://kea01/", "password": "s3cret"}, "password"),
    ],
)
def test_a_server_entry_carries_its_own_secrets(entry, key):
    server = server_from(entry)
    assert server.secret(key) == "s3cret"
    # Nothing is shared: another server of the same kind has none.
    assert server_from(entry["uri"]).secret(key) is None


def test_no_secret_is_read_from_the_environment(monkeypatch):
    for name in (
        "PIXIE_WINDHCP_PASSWORD",
        "PIXIE_WINDHCP_USER",
        "PIXIE_DHCPD_OMAPI_KEY",
    ):
        monkeypatch.setenv(name, "from-env")
    assert connect("windhcp://dhcp01/?transport=winrm").secret("password") is None
    assert connect("dhcpd://dhcp01").secret("omapi_key") is None


def test_the_entrys_secret_overrides_the_uris():
    server = server_from(
        {"uri": "windhcp://admin:from-uri@dhcp01/?transport=ssh", "password": "x"}
    )
    assert server.secret("password") == "x"
    assert server.secret("user") == "admin", "what the entry leaves is the URI's"


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"uri": "windhcp://dhcp01/", "omapi_key": "k"}, "unknown key.*omapi_key"),
        ({"password": "x"}, "needs|mapping with `uri:`"),
        (["windhcp://dhcp01/"], "not list"),
        ({"uri": "nosuch://h/"}, "nosuch"),
    ],
)
def test_a_bad_server_entry_is_a_config_error(entry, message):
    with pytest.raises(netboot.PixieConfigError, match=message):
        server_from(entry)


def test_a_bad_entry_never_echoes_its_secret():
    with pytest.raises(netboot.PixieConfigError) as refused:
        server_from({"uri": "windhcp://dhcp01/", "password": "s3cret", "pin": "1"})
    assert "s3cret" not in str(refused.value)


def test_the_config_file_can_take_a_secret_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NETBOOT_TEST_WINDHCP_PASSWORD", "s3cret")
    (tmp_path / "pixie.yaml").write_text(
        "dhcpzones:\n"
        "  lan:\n"
        "    network: 10.0.0.0/24\n"
        "    dhcpservers:\n"
        "      - uri: windhcp://dhcp01/?transport=winrm\n"
        '        password: "{{ env.NETBOOT_TEST_WINDHCP_PASSWORD }}"\n',
        encoding="utf-8",
    )
    from netboot.main import _load_config

    conf = _load_config(types.SimpleNamespace(config=str(tmp_path / "pixie.yaml")))
    zone = DHCPZone(**conf["dhcpzones"]["lan"])
    assert zone.dhcpservers[0].secret("password") == "s3cret"


def test_zone_scope_carries_its_overrides():
    from netboot.dhcp import scope_for

    zone = DHCPZone(_id="lan", network="10.0.0.0/24", subnet_id="7", scope="10.0.0.0")
    scope = scope_for(zone)
    assert scope.name == "lan"
    assert str(scope.network) == "10.0.0.0/24"
    assert scope.subnet_id == 7
    assert scope.scope_id == "10.0.0.0"
    plain = scope_for(DHCPZone(_id="lan", network="10.0.0.0/24"))
    assert plain.subnet_id is None and plain.scope_id is None
