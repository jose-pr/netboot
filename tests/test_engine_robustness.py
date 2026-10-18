"""Engine behaviour under partly-broken input and partly-failing backends.

Each case here is a crash or a silent wrong answer the 2026-09-17 review
reproduced: an offline DHCP server, a retired image blocking cleanup, a global
that replaced the render context, a zone chosen by declaration order.
"""

import logging

import dhcpctl
import pytest

import netboot


class _Recorder(dhcpctl.DHCPServer):
    """Records calls; `fail_on` makes one operation raise."""

    calls: list = []

    def __init__(self, uri: str, **kwargs):
        super().__init__(uri, **kwargs)
        self.fail_on = None

    def add(self, reservation):
        _Recorder.calls.append(("arm", self.uri))
        if self.fail_on == "add":
            raise RuntimeError(f"{self} refused to arm")

    def remove(self, reservation):
        _Recorder.calls.append(("disarm", self.uri))
        if self.fail_on == "remove":
            raise RuntimeError(f"{self} refused to disarm")


@pytest.fixture(autouse=True)
def _reset_calls():
    _Recorder.calls = []
    dhcpctl.register_backend("rollback", _Recorder)
    yield
    dhcpctl.unregister_backend("rollback")
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


def test_arming_survives_a_failing_backend_with_a_warning(caplog):
    engine = _engine()
    zone = engine.dhcpzones["lan"]
    zone.dhcpservers[0].fail_on = "add"
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.initialize(engine.lookup_target("host1"))
    # a is offline; b is still armed and nothing is rolled back.
    assert _Recorder.calls == [("arm", "rollback://a"), ("arm", "rollback://b")]
    assert "could not arm host1 on rollback://a" in caplog.text


def test_a_failing_backend_is_logged_without_its_password(caplog):
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": ["rollback://u:s3cret@a", "rollback://b"],
            }
        }
    )
    engine.dhcpzones["lan"].dhcpservers[0].fail_on = "add"
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.initialize(engine.lookup_target("host1"))
    assert "could not arm host1 on rollback://u:***@a" in caplog.text
    assert "s3cret" not in caplog.text


def test_arming_raises_when_every_backend_fails():
    engine = _engine()
    for server in engine.dhcpzones["lan"].dhcpservers:
        server.fail_on = "add"
    with pytest.raises(netboot.PixieError, match="rollback://a refused to arm") as e:
        engine.initialize(engine.lookup_target("host1"))
    assert isinstance(e.value.__cause__, RuntimeError), "chained to the first"
    assert _Recorder.calls == [("arm", "rollback://a"), ("arm", "rollback://b")]


def test_disarm_failures_are_warnings(caplog):
    engine = _engine()
    for server in engine.dhcpzones["lan"].dhcpservers:
        server.fail_on = "remove"
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.complete(engine.lookup_target("host1"))
    # Every backend is tried, and none of the failures is raised.
    assert _Recorder.calls == [
        ("disarm", "rollback://a"),
        ("disarm", "rollback://b"),
    ]
    assert "could not disarm host1 on rollback://a" in caplog.text
    assert "could not disarm host1 on rollback://b" in caplog.text


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
    assert engine.lookup_image("debian") is None


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


def _netboot_two_targets_one_image(templates_dir):
    config = {
        "templates": [templates_dir],
        "images": {"debian": {"template_path": [], "globals": {"kernel": "vmlinuz"}}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"},
            "host2": {"hostname": "host2", "ip": "10.0.0.6", "image": "debian"},
        },
    }
    return netboot.Pixie(**config)


def test_image_globals_survive_second_target(tmp_path):
    # make_context must not delattr globals off the shared image.
    d = tmp_path / "templates"
    d.mkdir()
    p = _netboot_two_targets_one_image(d)
    ctx1 = p.make_context(p.lookup_target("host1"))
    ctx2 = p.make_context(p.lookup_target("host2"))
    assert getattr(ctx1, "kernel", None) == "vmlinuz"
    assert getattr(ctx2, "kernel", None) == "vmlinuz"  # not dropped for host2


class _Boom:
    def __init__(self, _id=None, **kw):
        raise ValueError("boom")


class _BoomPixie(netboot.Pixie):
    things: "dict[str, _Boom]"


def test_valctr_typeerror_only_not_bare_except():
    # A non-TypeError in a config value ctor must propagate, rather
    # than being swallowed by a bare except and retried without _id.
    with pytest.raises(ValueError):
        _BoomPixie(things={"x": {}})


def test_engine_globals_are_isolated_from_the_caller_config():
    # The deepcopy in __init__ was overwritten by the annotated-attribute loop,
    # so nested globals stayed shared with the caller's config dict.
    config = {"globals": {"nested": {"k": "v"}}, "targets": {}, "images": {}}
    engine = netboot.Pixie(**config)
    engine.globals["nested"]["k"] = "changed"
    assert config["globals"]["nested"]["k"] == "v"


def test_underscore_prefixed_globals_are_kept():
    # `_`-prefixed keys are skipped for collections (ids), but a global named
    # `_internal` is just a variable name.
    engine = netboot.Pixie(globals={"_internal": 1, "plain": 2})
    assert engine.globals == {"_internal": 1, "plain": 2}


def test_a_subclass_attribute_keeps_its_class_default():
    class _WithDefault(netboot.Pixie):
        label: str = "default-label"

    engine = _WithDefault(targets={}, images={}, dhcpzones={})
    # An annotated attribute the config does not mention keeps its default.
    assert engine.label == "default-label"


def test_an_optional_annotation_does_not_crash_construction():
    import typing

    class _WithOptional(netboot.Pixie):
        note: typing.Optional[str] = None

    engine = _WithOptional(note="hello", targets={}, images={}, dhcpzones={})
    # `Optional[str]` is not callable; coercion must skip it rather than raise.
    assert engine.note == "hello"


def test_a_zone_with_no_servers_warns_that_nothing_was_armed(caplog):
    engine = netboot.Pixie(
        images={"debian": {}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.initialize(engine.lookup_target("host1"))
    assert "zone lan has no dhcpservers: nothing was armed for host1" in caplog.text


@pytest.mark.parametrize("name", ["version", "render", "searchpaths", "resource"])
def test_a_global_named_like_a_context_member_is_dropped(caplog, name):
    engine = netboot.Pixie(
        globals={name: "12.4", "site": "lab"},
        images={"debian": {}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )
    with caplog.at_level(logging.WARNING, logger="netboot"):
        ctx = engine.make_context(engine.lookup_target("host1"))
    assert f"ignoring global(s) {name}" in caplog.text
    assert getattr(ctx, name) != "12.4" and ctx.site == "lab"


def test_hooks_none_is_no_hooks():
    assert netboot.Pixie(hooks=None).targets == {}


def test_a_resource_with_no_path_resolves_to_none():
    engine = netboot.Pixie(
        images={"debian": {}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        repos={"mirror": {"services": {"http": "http://m/"}}},
        targets={"host1": {"ip": "10.0.0.5", "image": "debian"}},
    )
    ctx = engine.make_context(engine.lookup_target("host1"))
    assert ctx.resource(netboot.content.Resource(src="mirror")) is None
