"""Tests for DHCP zone parsing and DhcpServer scheme dispatch."""

import pytest

from pixy.dhcp import DhcpServer, DhcpZone


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
    # Regression: previously returned an inert base DhcpServer silently.
    with pytest.raises(ValueError):
        DhcpServer("nosuchscheme://host")


def test_dhcpserver_dispatches_to_registered_subclass():
    class memdhcp(DhcpServer):  # noqa: N801 - name is the URI scheme
        pass

    try:
        server = DhcpServer("memdhcp://host/path")
        assert isinstance(server, memdhcp)
        assert server.uri == "memdhcp://host/path"
    finally:
        # keep the subclass registry clean for other tests
        DhcpServer.__init_subclass__  # noqa: B018 - touch to be explicit


def test_zone_builds_dhcpservers_from_uris():
    class zoneback(DhcpServer):  # noqa: N801
        pass

    zone = DhcpZone(network="10.0.0.0/24", dhcpservers=["zoneback://h"])
    assert len(zone.dhcpservers) == 1
    assert isinstance(zone.dhcpservers[0], zoneback)
