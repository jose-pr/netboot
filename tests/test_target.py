"""Tests for PixieTarget id/hostname/ip/mac resolution."""

import netboot


def test_target_id_is_ip_sets_ip():
    # Regression: id-is-IP branch used to no-op (`self.ip = self.ip`).
    target = netboot.PixieTarget(_id="10.0.0.42")
    assert str(target.ip) == "10.0.0.42"
    assert target.hostname == ""


def test_target_id_is_mac_sets_mac():
    target = netboot.PixieTarget(_id="aa:bb:cc:dd:ee:ff")
    assert target.mac.as_str() == "aa:bb:cc:dd:ee:ff"


def test_target_id_is_hostname_resolves_ip(monkeypatch):
    monkeypatch.setattr(
        netboot.netutils,
        "resolve",
        lambda name, *a, **kw: [netboot.netutils.parse("192.0.2.10")],
    )
    target = netboot.PixieTarget(_id="host1")
    assert target.hostname == "host1"
    assert str(target.ip) == "192.0.2.10"


def test_target_hostname_lowercased():
    target = netboot.PixieTarget(_id="10.0.0.1", hostname="HostUP")
    assert target.hostname == "hostup"


def test_target_empty_ip_stays_falsy():
    # A bare-MAC id with no hostname/ip: ip must remain falsy, not crash.
    target = netboot.PixieTarget(_id="aa:bb:cc:dd:ee:ff")
    assert not target.ip


def test_unresolvable_hostname_makes_exactly_one_lookup(monkeypatch):
    # Regression: the fill-in used to loop while the name stayed unresolved, so
    # a target not (yet) in DNS hung every command and flooded the resolver.
    calls = []

    def _empty(name, *a, **kw):
        calls.append(name)
        return []

    monkeypatch.setattr(netboot.netutils, "resolve", _empty)
    target = netboot.PixieTarget(_id="newhost")
    assert calls == ["newhost"]
    assert target.hostname == "newhost"
    assert not target.ip


def test_unresolvable_hostname_does_not_block_other_targets(monkeypatch):
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
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

    monkeypatch.setattr(netboot.netutils, "resolve", _raises)
    assert not netboot.PixieTarget(_id="not a hostname").ip
