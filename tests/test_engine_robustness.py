"""Engine behaviour under partly-broken input and partly-failing backends.

Each case here is a crash or a silent wrong answer the 2026-09-17 review
reproduced: a half-armed target, a retired image blocking cleanup, a global
that replaced the render context, a zone chosen by declaration order.
"""

import logging

import pytest

import netboot
from netboot.dhcp import DhcpServer


class _Recorder(DhcpServer):
    """Records calls; `fail_on` makes one operation raise."""

    calls: list = []

    def __init__(self, uri: str):
        super().__init__(uri)
        self.fail_on = None

    def add_target(self, ctx):
        _Recorder.calls.append(("arm", self.uri))
        if self.fail_on == "add":
            raise RuntimeError(f"{self.uri} refused to arm")

    def remove_target(self, ctx):
        _Recorder.calls.append(("disarm", self.uri))
        if self.fail_on == "remove":
            raise RuntimeError(f"{self.uri} refused to disarm")


@pytest.fixture(autouse=True)
def _reset_calls():
    _Recorder.calls = []
    yield
    _Recorder.calls = []


def _engine(**overrides):
    config = {
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": ["rollback://a", "rollback://b"],
            }
        },
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
    }
    config.update(overrides)
    return netboot.Pixie(**config)


class rollback(_Recorder):  # scheme `rollback://`
    pass


def test_a_failing_backend_rolls_back_the_ones_already_armed():
    engine = _engine()
    zone = engine.dhcpzones["lan"]
    zone.dhcpservers[1].fail_on = "add"
    with pytest.raises(RuntimeError):
        engine.initialize(engine.lookup_target("host1"))
    # b failed, so a must not be left armed.
    assert _Recorder.calls == [
        ("arm", "rollback://a"),
        ("arm", "rollback://b"),
        ("disarm", "rollback://a"),
    ]


def test_disarm_continues_past_a_failure_then_raises():
    engine = _engine()
    zone = engine.dhcpzones["lan"]
    zone.dhcpservers[0].fail_on = "remove"
    with pytest.raises(RuntimeError):
        engine.complete(engine.lookup_target("host1"))
    # The second backend is still disarmed despite the first failing.
    assert ("disarm", "rollback://b") in _Recorder.calls


def test_complete_works_when_the_image_is_no_longer_configured(caplog):
    engine = _engine(images={})
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.complete(engine.lookup_target("host1"))
    assert ("disarm", "rollback://a") in _Recorder.calls
    assert "image" in caplog.text


def test_initialize_still_requires_the_image():
    engine = _engine(images={})
    with pytest.raises(netboot.PixieLookupError):
        engine.initialize(engine.lookup_target("host1"))


@pytest.mark.parametrize("reserved", ["target", "image", "dhcpzone", "repos"])
def test_a_global_cannot_replace_a_context_field(reserved, caplog):
    engine = _engine(globals={reserved: "hijacked"})
    with caplog.at_level(logging.WARNING, logger="netboot"):
        ctx = engine.make_context(engine.lookup_target("host1"))
    assert getattr(ctx, reserved) != "hijacked"
    assert reserved in caplog.text


def test_the_most_specific_zone_wins_over_declaration_order():
    engine = _engine(
        dhcpzones={
            "wide": {"network": "10.0.0.0/16"},
            "narrow": {"network": "10.0.0.0/24"},
        }
    )
    target = engine.lookup_target("host1")
    assert engine.lookup_dhcpzone("", target) is engine.dhcpzones["narrow"]


def test_a_null_config_entry_means_defaults_not_a_crash():
    # `targets: {host1:}` is valid YAML and reads as "all defaults".
    engine = netboot.Pixie(targets={"10.0.0.7": None}, images={}, dhcpzones={})
    assert str(engine.targets["10.0.0.7"].ip) == "10.0.0.7"


def test_a_non_mapping_config_entry_is_reported_clearly():
    with pytest.raises(netboot.PixieConfigError, match="expected a mapping"):
        netboot.Pixie(targets={"host1": 42}, images={}, dhcpzones={})


def test_image_match_returning_none_is_not_a_match():
    class _NullMatch(netboot.PixieImage):
        def match(self, name, check):
            return None

    engine = _engine()
    engine.images = {"debian": _NullMatch(_id="debian", template_path=[])}
    assert engine.lookup_image("debian") == {}


def test_zone_lists_are_copied_not_normalised_in_place():
    shared = ["10.0.0.53"]
    engine = _engine(
        dhcpzones={"lan": {"network": "10.0.0.0/24", "nameservers": shared}}
    )
    assert shared == ["10.0.0.53"]  # caller's list untouched
    assert str(engine.dhcpzones["lan"].nameservers[0]) == "10.0.0.53"


def test_an_unparseable_nameserver_is_dropped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine = _engine(
            dhcpzones={
                "lan": {
                    "network": "10.0.0.0/24",
                    "nameservers": ["10.0.0.53", "not-an-address"],
                }
            }
        )
    zone = engine.dhcpzones["lan"]
    assert [str(n) for n in zone.nameservers] == ["10.0.0.53"]
    assert "not-an-address" in caplog.text


def test_get_local_server_accepts_the_strings_config_holds():
    engine = _engine()
    zone = engine.dhcpzones["lan"]
    assert (
        str(zone.get_local_server(["192.0.2.1", "10.0.0.9"], "fallback")) == "10.0.0.9"
    )
    assert zone.get_local_server(["192.0.2.1"], "fallback") == "fallback"
    assert str(zone.get_local_server("10.0.0.9", "fallback")) == "10.0.0.9"
