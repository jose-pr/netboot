"""Tests for DHCP zone parsing and how a zone builds its servers."""

import dhcpctl
import pytest

import netboot
from netboot.dhcp import DhcpZone, connect


def test_zone_parses_network_and_nameservers():
    zone = DhcpZone(
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
    zone = DhcpZone(gateway="192.168.5.1/24")
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
    zone = DhcpZone(network="10.0.0.0/24", dhcpservers=["zoneback://h"])
    assert len(zone.dhcpservers) == 1
    assert isinstance(zone.dhcpservers[0], zoneback)


def test_zone_keeps_a_server_object_as_it_is(dhcp_backend):
    zoneback = dhcp_backend("zoneback")
    server = dhcpctl.connect("zoneback://h")
    zone = DhcpZone(network="10.0.0.0/24", dhcpservers=[server])
    assert zone.dhcpservers == [server]


@pytest.mark.parametrize(
    "uri, key, variable",
    [
        ("windhcp://dhcp01/?transport=ssh", "password", "PIXIE_WINDHCP_PASSWORD"),
        ("windhcp://dhcp01/?transport=ssh", "user", "PIXIE_WINDHCP_USER"),
        ("dhcpd://dhcp01", "omapi_key", "PIXIE_DHCPD_OMAPI_KEY"),
    ],
)
def test_secrets_are_read_from_the_environment_when_asked(
    monkeypatch, uri, key, variable
):
    server = connect(uri)
    assert server.secret(key) is None
    # Read at use, not at connect: `pixie dhcp-config --as` sets it later.
    monkeypatch.setenv(variable, "s3cret")
    assert server.secret(key) == "s3cret"
    assert server.secret("other") is None


def test_a_secret_in_the_uri_outranks_the_environment(monkeypatch):
    monkeypatch.setenv("PIXIE_WINDHCP_PASSWORD", "from-env")
    server = connect("windhcp://admin:from-uri@dhcp01/?transport=ssh")
    assert server.secret("password") == "from-uri"
    assert connect("windhcp://admin@dhcp01/?transport=ssh").secret("password") == (
        "from-env"
    )


def test_a_scheme_without_secrets_reads_none(dhcp_backend, monkeypatch):
    dhcp_backend("plain")
    monkeypatch.setenv("PIXIE_WINDHCP_PASSWORD", "s3cret")
    assert connect("plain://host").secret("password") is None


def test_zone_scope_carries_its_overrides():
    from netboot.dhcp import scope_for

    zone = DhcpZone(_id="lan", network="10.0.0.0/24", subnet_id="7", scope="10.0.0.0")
    scope = scope_for(zone)
    assert scope.name == "lan"
    assert str(scope.network) == "10.0.0.0/24"
    assert scope.subnet_id == 7
    assert scope.scope_id == "10.0.0.0"
    plain = scope_for(DhcpZone(_id="lan", network="10.0.0.0/24"))
    assert plain.subnet_id is None and plain.scope_id is None
