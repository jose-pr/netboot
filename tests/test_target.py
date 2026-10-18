"""Tests for PixieTarget id/hostname/ip/mac resolution."""

import logging

import netimps
import pytest

import netboot


def test_target_id_is_ip_sets_ip():
    # An IP-shaped id fills `ip`.
    target = netboot.PixieTarget(_id="10.0.0.42")
    assert str(target.ip) == "10.0.0.42"
    assert target.hostname == ""


def test_target_id_is_mac_sets_mac():
    target = netboot.PixieTarget(_id="aa:bb:cc:dd:ee:ff")
    assert target.mac.format() == "aa:bb:cc:dd:ee:ff"


def test_target_id_is_hostname_resolves_ip(monkeypatch):
    monkeypatch.setattr(
        netimps,
        "resolve",
        lambda name, *a, **kw: [netimps.parse("192.0.2.10")],
    )
    target = netboot.PixieTarget(_id="host1")
    assert target.hostname == "host1" and target.ip == ""
    assert str(target.resolve_ip().ip) == "192.0.2.10"


def test_target_hostname_lowercased():
    target = netboot.PixieTarget(_id="10.0.0.1", hostname="HostUP")
    assert target.hostname == "hostup"


def test_target_empty_ip_stays_falsy():
    # A bare-MAC id with no hostname/ip: ip must remain falsy, not crash.
    target = netboot.PixieTarget(_id="aa:bb:cc:dd:ee:ff")
    assert not target.ip


def test_unresolvable_hostname_makes_exactly_one_lookup(monkeypatch):
    # One lookup, never a loop: a name not (yet) in DNS answers the same every
    # time, and retrying would hang the command and flood the resolver.
    calls = []

    def _empty(name, *a, **kw):
        calls.append(name)
        return []

    monkeypatch.setattr(netimps, "resolve", _empty)
    target = netboot.PixieTarget(_id="newhost")
    target.resolve_ip()
    target.resolve_ip()
    assert calls == ["newhost"]
    assert target.hostname == "newhost"
    assert not target.ip


def test_unresolvable_hostname_does_not_block_other_targets(monkeypatch):
    monkeypatch.setattr(netimps, "resolve", lambda name, *a, **kw: [])
    engine = netboot.Pixie(
        targets={
            "newhost": {"image": "debian"},
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"},
        }
    )
    assert str(engine.lookup_target("host1").ip) == "10.0.0.5"


def test_malformed_hostname_does_not_abort_construction(monkeypatch):
    def _raises(name, *a, **kw):
        raise ValueError(f"malformed query: {name!r}")

    monkeypatch.setattr(netimps, "resolve", _raises)
    assert not netboot.PixieTarget(_id="not a hostname").resolve_ip().ip


def test_building_the_engine_looks_nothing_up(monkeypatch):
    calls = []

    def _record(name, *a, **kw):
        calls.append(name)
        return [netimps.parse("10.0.0.7")]

    monkeypatch.setattr(netimps, "resolve", _record)
    engine = netboot.Pixie(
        targets={"web01": {}, "db01": {}, "app01": {"ip": "10.0.0.8"}},
    )
    assert calls == []
    assert str(engine.lookup_target("web01").ip) == "10.0.0.7"
    assert calls == ["web01"], "only the target asked for"
    assert engine.lookup_target("10.0.0.8")._id == "app01"
    assert calls == ["web01"], "a configured address needs no lookup"


def test_an_address_query_finds_a_hostname_only_target(monkeypatch):
    addresses = {"web01": "10.0.0.7", "db01": "10.0.0.9"}
    monkeypatch.setattr(
        netimps, "resolve", lambda name, *a, **kw: [netimps.parse(addresses[name])]
    )
    engine = netboot.Pixie(targets={"web01": {}, "db01": {}})
    assert engine.lookup_target("10.0.0.9")._id == "db01"


def test_a_numeric_mac_is_refused_not_reinterpreted():
    # YAML 1.1 reads `mac: 52:54:00:12:34:56` as the base-60 int 41135085296,
    # which parses as 00:09:93:d7:96:f0: arming that is arming another machine.
    with pytest.raises(netboot.PixieConfigError, match="'web01'.*quote it"):
        netboot.PixieTarget(_id="web01", mac=41135085296)


def test_a_malformed_ip_is_an_error_not_a_missing_ip():
    with pytest.raises(ValueError, match="10.0.0.500"):
        netboot.PixieTarget(_id="web01", ip="10.0.0.500")


def test_no_address_has_one_spelling():
    assert netboot.PixieTarget(_id="aa:bb:cc:dd:ee:ff", ip=None).ip == ""


def test_the_engine_names_the_target_whose_value_is_wrong():
    with pytest.raises(netboot.PixieConfigError, match="targets.web01: .*10.0.0.500"):
        netboot.Pixie(targets={"web01": {"ip": "10.0.0.500"}})


def test_a_non_text_target_id_is_refused():
    with pytest.raises(netboot.PixieConfigError, match="the id 7 is not text"):
        netboot.Pixie(targets={7: {"ip": "10.0.0.5"}})


def test_unknown_keys_are_warned_about_by_their_dotted_name(caplog):
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine = netboot.Pixie(
            targetz={},
            _private={},
            defaults={"targets": {"imgae": "x"}},
            targets={"web01": {"ip": "10.0.0.5", "dhcp_options": {}, "imgae": "x"}},
            dhcpzones={"lan": {"network": "10.0.0.0/24", "gatway": "10.0.0.1"}},
        )
    named = {
        line.split("config key ")[1].split(" ")[0]
        for line in caplog.messages
        if "config key" in line
    }
    assert named == {
        "targetz",
        "defaults.targets.imgae",
        "targets.web01.imgae",
        "dhcpzones.lan.gatway",
    }
    assert engine.dhcpzones["lan"].gateway is None


def test_known_keys_raise_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="netboot"):
        netboot.Pixie(
            templates=[],
            dhcp={},
            images={"debian": {"template_path": [], "dhcp_when": {}, "src": "r"}},
            dhcpzones={"lan": {"network": "10.0.0.0/24", "subnet_id": 1, "scope": "s"}},
            targets={
                "web01": {
                    "ip": "10.0.0.5",
                    "image": "debian",
                    "mac": "aa-bb-cc-dd-ee-ff",
                }
            },
        )
    assert "config key" not in caplog.text
