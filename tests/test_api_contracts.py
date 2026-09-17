"""Tests for documented API that had no coverage at all.

Everything here is promised by the shipped header (`src/netboot/AGENTS.md`)
and was never exercised, so a change would have broken a consumer silently.
"""

import logging

import pytest

import netboot
from netboot.content import Repository, Resource
from netboot.dhcp import DhcpServer, DhcpZone
from netboot.utils import flatten
from netboot.utils.net import Host


def test_resource_division_joins_the_path_and_keeps_the_repo():
    resource = Resource(path="images", src="mirror")
    joined = resource / "vmlinuz"
    assert str(joined.path) == "images/vmlinuz"
    assert joined.src == "mirror"
    assert str(resource.path) == "images", "the original must not be mutated"


def test_host_equality_and_hashing_use_the_address():
    assert Host("mirror.example") == Host("mirror.example")
    assert Host("mirror.example") != Host("other.example")
    assert len({Host("a"), Host("a"), Host("b")}) == 2
    assert Host(Host("wrapped")).address == "wrapped"
    assert Host(None).address == ""
    # Comparing with a non-Host is NotImplemented, so Python falls back.
    assert (Host("a") == "a") is False


def test_dhcpserver_default_scheme_handles_a_schemeless_uri(dhcp_backend):
    dhcp_backend("bare")

    class _Defaulting(DhcpServer):
        DEFAULT_SCHEME = "bare"

    server = _Defaulting("no-scheme-here")
    assert isinstance(server, DhcpServer)
    assert server.uri == "no-scheme-here"


def test_an_unknown_scheme_names_the_fix():
    with pytest.raises(ValueError, match="--load-module"):
        DhcpServer("nosuchscheme://host")


def test_new_pixie_object_hook_can_swap_the_class():
    class _Custom(netboot.Pixie):
        pass

    def swap(event, engine, value, kwargs):
        if event is netboot.PixieEvent.NewPixieObject:
            return _Custom
        return value

    engine = netboot.Pixie(hooks=[swap], targets={}, images={}, dhcpzones={})
    assert isinstance(engine, _Custom)


def test_a_new_pixie_object_hook_returning_a_non_class_is_reported():
    def broken(event, engine, value, kwargs):
        return "not a class" if event is netboot.PixieEvent.NewPixieObject else value

    with pytest.raises(netboot.PixieConfigError, match="NewPixieObject"):
        netboot.Pixie(hooks=[broken], targets={}, images={}, dhcpzones={})


def test_event_values_carry_the_documented_prefix():
    # Hooks compare against these strings, so the prefix is contract.
    assert netboot.PixieEvent.LookupTarget.value == "PixieEvent.LookupTarget"
    assert str(netboot.PixieEvent.FoundTarget) == "PixieEvent.FoundTarget"


def test_zone_nameserver_is_the_first_or_empty():
    zone = DhcpZone(network="10.0.0.0/24", nameservers=["10.0.0.53", "10.0.0.54"])
    assert str(zone.nameserver) == "10.0.0.53"
    assert DhcpZone(network="10.0.0.0/24").nameserver == ""


def test_zone_derives_its_network_from_a_cidr_gateway():
    zone = DhcpZone(gateway="10.0.5.1/24")
    assert str(zone.network) == "10.0.5.0/24"
    assert str(zone.gateway) == "10.0.5.1"


def test_flatten_warns_when_two_sources_collide(caplog):
    # `{"target": {"ip": x}}` and a global `target_ip` both flatten to
    # `target_ip`, and the last one silently won.
    with caplog.at_level(logging.WARNING, logger="netboot"):
        flat = flatten({"target": {"ip": "10.0.0.5"}, "target_ip": "192.0.2.1"})
    assert flat["target_ip"] == "192.0.2.1"
    assert "target_ip" in caplog.text


def test_repository_getitem_is_get_with_the_service():
    repo = Repository(address="10.0.0.1", services={"tftp": "/boot"}, local="/srv/tftp")
    assert str(repo["images/vmlinuz", "tftp"]) == str(
        repo.get("images/vmlinuz", service="tftp")
    )


def test_context_version_is_the_documented_shape():
    engine = netboot.Pixie(
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )
    ctx = engine.make_context(engine.lookup_target("host1"))
    assert ctx.version == f"netboot-v{netboot.__version__}"
    assert engine.VERSION == netboot.__version__
