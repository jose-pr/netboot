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
        netboot.netutils, "resolve", lambda name, *a, **kw: [netboot.netutils.parse("192.0.2.10")]
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
