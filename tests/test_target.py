"""Tests for PixyTarget id/hostname/ip/mac resolution."""

import pixy


def test_target_id_is_ip_sets_ip():
    # Regression: id-is-IP branch used to no-op (`self.ip = self.ip`).
    target = pixy.PixyTarget(_id="10.0.0.42")
    assert str(target.ip) == "10.0.0.42"
    assert target.hostname == ""


def test_target_id_is_mac_sets_mac():
    target = pixy.PixyTarget(_id="aa:bb:cc:dd:ee:ff")
    assert target.mac.as_str() == "aa:bb:cc:dd:ee:ff"


def test_target_id_is_hostname_resolves_ip(monkeypatch):
    monkeypatch.setattr(pixy.netutils, "nslookup", lambda name, **kw: ["192.0.2.10"])
    target = pixy.PixyTarget(_id="host1")
    assert target.hostname == "host1"
    assert str(target.ip) == "192.0.2.10"


def test_target_hostname_lowercased():
    target = pixy.PixyTarget(_id="10.0.0.1", hostname="HostUP")
    assert target.hostname == "hostup"


def test_target_empty_ip_stays_falsy():
    # A bare-MAC id with no hostname/ip: ip must remain falsy, not crash.
    target = pixy.PixyTarget(_id="aa:bb:cc:dd:ee:ff")
    assert not target.ip
