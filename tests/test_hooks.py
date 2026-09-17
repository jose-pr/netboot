"""Tests for the hook chain and the initialize/complete lifecycle.

Hooks are the extension contract the README advertises and had no coverage.
The lifecycle is observed through a recording `DhcpServer` backend rather than
by talking to anything: no server is started and no socket is opened.
"""

import pytest

import netboot
from netboot.dhcp import DhcpServer

_HOOK_MODULE = """
CALLS = []


def hook(event, netboot, value, kwargs):
    CALLS.append(str(event))
    return value
"""


class recorder(DhcpServer):
    """A DhcpServer whose scheme is ``recorder://``; records arm/disarm calls."""

    #: (action, target id) for every call, across every instance.
    CALLS = []

    def add_target(self, ctx):
        recorder.CALLS.append(("add", ctx.target._id))

    def remove_target(self, ctx):
        recorder.CALLS.append(("remove", ctx.target._id))


def _config(tmp_path, **extra):
    templates = tmp_path / "templates"
    templates.mkdir(exist_ok=True)
    config = {
        "templates": [templates],
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {
            "lan": {"network": "10.0.0.0/24", "dhcpservers": ["recorder://lan"]}
        },
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
    }
    config.update(extra)
    return config


def _recording_hook():
    calls = []

    def hook(event, netboot_, value, kwargs):
        calls.append((str(event), value))
        return value

    return hook, calls


def test_hooks_see_the_construction_events(tmp_path):
    hook, calls = _recording_hook()
    netboot.Pixie(hooks=[hook], **_config(tmp_path))
    events = [event for event, _ in calls]
    assert events[0] == "PixieEvent.NewPixieObject"
    assert events[1] == "PixieEvent.StartPixieInit"
    assert events[-1] == "PixieEvent.PixieInitiated"
    assert "PixieEvent.SetPixieProperty" in events


def test_hook_value_threads_through_the_chain(tmp_path):
    seen = []

    def first(event, netboot_, value, kwargs):
        if event == netboot.PixieEvent.LookupTarget:
            return "host1"
        return value

    def second(event, netboot_, value, kwargs):
        if event == netboot.PixieEvent.LookupTarget:
            seen.append(value)  # the *first* hook's return, not the original
        return value

    p = netboot.Pixie(hooks=[first, second], **_config(tmp_path))
    target = p.lookup_target("does-not-exist")
    assert seen == ["host1"]
    assert target._id == "host1"


def test_lookup_target_hook_can_substitute_a_target(tmp_path):
    substitute = netboot.PixieTarget(_id="injected", ip="10.0.0.9", image="debian")

    def hook(event, netboot_, value, kwargs):
        if event == netboot.PixieEvent.LookupTarget:
            return substitute
        return value

    p = netboot.Pixie(hooks=[hook], **_config(tmp_path))
    # A str target never reaches the lookup loop: the hook already returned a
    # PixieTarget, and FoundTarget passes it through.
    assert p.lookup_target("host1") is substitute


def test_found_target_hook_result_must_be_a_target(tmp_path):
    def hook(event, netboot_, value, kwargs):
        if event == netboot.PixieEvent.FoundTarget:
            return "not-a-target"
        return value

    p = netboot.Pixie(hooks=[hook], **_config(tmp_path))
    assert p.lookup_target("host1") is None


def test_hooks_accept_import_strings(tmp_path, monkeypatch):
    module = tmp_path / "netboot_hook_probe.py"
    module.write_text(_HOOK_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    netboot.Pixie(hooks=["netboot_hook_probe.hook"], **_config(tmp_path))

    import netboot_hook_probe

    assert "PixieEvent.PixieInitiated" in netboot_hook_probe.CALLS


def test_initialize_arms_every_dhcp_server_in_the_zone(tmp_path):
    recorder.CALLS.clear()
    p = netboot.Pixie(**_config(tmp_path))
    ctx = p.initialize(p.lookup_target("host1"))
    assert recorder.CALLS == [("add", "host1")]
    assert ctx.dhcpzone is p.dhcpzones["lan"]


def test_complete_disarms_every_dhcp_server_in_the_zone(tmp_path):
    recorder.CALLS.clear()
    p = netboot.Pixie(**_config(tmp_path))
    p.complete(p.lookup_target("host1"))
    assert recorder.CALLS == [("remove", "host1")]


def test_initialize_emits_the_documented_event_sequence(tmp_path):
    hook, calls = _recording_hook()
    p = netboot.Pixie(hooks=[hook], **_config(tmp_path))
    calls.clear()  # drop the construction events
    p.initialize(p.lookup_target("host1"))
    events = [event for event, _ in calls]
    assert events == [
        "PixieEvent.LookupTarget",
        "PixieEvent.FoundTarget",
        "PixieEvent.StartPixieInitialize",
        "PixieEvent.FoundTargetImage",
        "PixieEvent.FoundTargetDhcpzone",
        "PixieEvent.PixieContextForTarget",
        "PixieEvent.EndPixieInitialize",
    ]


def test_complete_emits_the_documented_event_sequence(tmp_path):
    hook, calls = _recording_hook()
    p = netboot.Pixie(hooks=[hook], **_config(tmp_path))
    target = p.lookup_target("host1")
    calls.clear()
    p.complete(target)
    events = [event for event, _ in calls]
    assert events[0] == "PixieEvent.StartPixieComplete"
    assert events[-1] == "PixieEvent.EndPixieComplete"


def test_unknown_dhcp_scheme_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="No DhcpServer backend registered"):
        netboot.Pixie(
            **_config(
                tmp_path,
                dhcpzones={
                    "lan": {"network": "10.0.0.0/24", "dhcpservers": ["nosuch://x"]}
                },
            )
        )


def test_start_init_hook_result_is_what_gets_built():
    # The returned config was stored but the original was read, so a hook
    # written in the natural non-mutating style had its targets ignored while
    # its templates took effect -- the config ended up half applied.
    def add_a_target(event, engine, value, kwargs):
        if event is not netboot.PixieEvent.StartPixieInit:
            return value
        merged = dict(value)
        merged["targets"] = dict(merged.get("targets") or {})
        merged["targets"]["injected"] = {"ip": "10.0.0.99", "image": "debian"}
        return merged

    engine = netboot.Pixie(
        hooks=[add_a_target],
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={},
    )
    assert engine.lookup_target("injected") is not None


def test_a_start_init_hook_returning_none_is_reported():
    def drops_it(event, engine, value, kwargs):
        # Passes other events through; only drops the config it is handed.
        return None if event is netboot.PixieEvent.StartPixieInit else value

    with pytest.raises(netboot.PixieConfigError, match="StartPixieInit"):
        netboot.Pixie(hooks=[drops_it], targets={}, images={}, dhcpzones={})
