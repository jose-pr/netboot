"""`dhcp_complete:` — what a finished target's entry becomes.

The failure this prevents: a machine that still receives `boot-file-name` after
its install finishes boots the installer again. Removing the reservation stops
that and throws away the fixed address; keeping it without boot options, or with
a `sanboot` script, stops it and does not.
"""

import logging

import pytest

import netboot
from netboot.dhcp import DhcpServer
from netboot.dhcp.completion import BOOT_OPTIONS, build_completion

SANBOOT = {"keep": True, "options": {"boot-file-name": "sanboot.ipxe"}}


def _engine(image_extra=None, **overrides):
    image = {
        "template_path": [],
        "dhcp_options": {
            "boot-file-name": "undionly.kpxe",
            "tftp-server-name": "10.0.0.2",
        },
    }
    image.update(image_extra or {})
    config = {
        "images": {"debian": image},
        "dhcpzones": {
            "lan": {"network": "10.0.0.0/24", "gateway": "10.0.0.1", "subnet_id": 1}
        },
        "targets": {
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    }
    config.update(overrides)
    return netboot.Pixie(**config)


def _ctx(engine=None):
    engine = engine or _engine()
    return engine.make_context(engine.lookup_target("web01"))


# -- the model --------------------------------------------------------------


def test_removing_the_entry_is_the_default():
    completion = build_completion(_ctx())
    assert completion.keep is False
    assert not completion


def test_keep_without_options_drops_only_the_boot_options():
    ctx = _ctx(_engine({"dhcp_complete": {"keep": True}}))
    completion = build_completion(ctx)
    kept = completion.apply_to(DhcpServer("windhcp://dhcp01/").options_for(ctx))
    assert not any(name in kept for name in BOOT_OPTIONS)
    # The options that make a reservation worth keeping are still there.
    assert kept["router"] is not None and "subnet-mask" in kept


def test_keep_with_options_replaces_rather_than_merges():
    ctx = _ctx(_engine({"dhcp_complete": SANBOOT}))
    completion = build_completion(ctx)
    kept = completion.apply_to(DhcpServer("windhcp://dhcp01/").options_for(ctx))
    assert kept["boot-file-name"] == "sanboot.ipxe"
    # A merge would have left the installer's tftp server in place.
    assert "tftp-server-name" not in kept


def test_a_target_overrides_the_image_completion():
    engine = _engine(
        {"dhcp_complete": SANBOOT},
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
                "dhcp_complete": {"keep": False},
            }
        },
    )
    assert build_completion(_ctx(engine)).keep is False


@pytest.mark.parametrize(
    "declared, message",
    [
        ("remove", "must be a mapping"),
        ({"keep": "yes"}, "must be true or false"),
        ({"keep": True, "zzz": 1}, "unknown key"),
        ({"keep": True, "options": {"nonsense": "x"}}, "unknown dhcp option"),
        ({"options": {"boot-file-name": "x"}}, "nowhere to go"),
    ],
)
def test_a_malformed_completion_names_the_mistake(declared, message):
    with pytest.raises(netboot.PixieConfigError, match=message):
        build_completion(_ctx(_engine({"dhcp_complete": declared})))


# -- windhcp ---------------------------------------------------------------


def _sent(uri, engine):
    server = DhcpServer(uri)
    sent = []
    server.run = lambda payload, body: sent.append((payload, body))
    server.complete_target(_ctx(engine))
    return sent


def test_windhcp_default_completion_removes_the_reservation():
    sent = _sent("windhcp://dhcp01/", _engine())
    assert "ClientId" in sent[0][0]
    assert "Remove-DhcpServerv4Reservation" in "\n".join(sent[0][1])


def test_windhcp_keep_sets_the_completion_options_and_removes_the_rest():
    sent = _sent("windhcp://dhcp01/", _engine({"dhcp_complete": SANBOOT}))
    payload, body = sent[0]
    assert {o["Id"]: o["Value"] for o in payload["Set"]}[67] == ["sanboot.ipxe"]
    assert payload["Remove"] == [66]
    assert "Remove-DhcpServerv4Reservation" not in "\n".join(body)
    # Already-absent is success: completion re-runs.
    assert "-ErrorAction SilentlyContinue" in "\n".join(body)


def test_windhcp_keep_bare_removes_every_boot_option():
    sent = _sent("windhcp://dhcp01/", _engine({"dhcp_complete": {"keep": True}}))
    payload = sent[0][0]
    assert sorted(payload["Remove"]) == [66, 67]
    assert sorted(o["Id"] for o in payload["Set"]) == [1, 3, 28]


# -- dhcpd -----------------------------------------------------------------


class _FakeOmapi:
    def __init__(self, calls):
        self.calls = calls

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def _dhcpd_calls(engine, uri="dhcpd://secret@dhcp01/"):
    server = DhcpServer(uri)
    calls = []
    server.connect = lambda: _FakeOmapi(calls)
    server.complete_target(_ctx(engine))
    return calls


def test_dhcpd_default_completion_deletes_the_host():
    calls = _dhcpd_calls(_engine())
    assert [c[0] for c in calls] == ["del_host", "close"]


def test_dhcpd_keep_supersedes_the_host_in_one_call():
    # One call, so there is no window with the installer's filename still set.
    calls = _dhcpd_calls(_engine({"dhcp_complete": SANBOOT}))
    assert [c[0] for c in calls] == ["add_host_supersede", "close"]
    statements = calls[0][2]["statements"]
    assert 'filename "sanboot.ipxe";' in statements
    assert "undionly" not in statements


def test_dhcpd_keep_bare_leaves_no_filename():
    calls = _dhcpd_calls(_engine({"dhcp_complete": {"keep": True}}))
    statements = calls[0][2]["statements"] or ""
    assert "filename" not in statements
    assert "option subnet-mask" in statements


# -- kea -------------------------------------------------------------------


def _kea_sent(engine):
    server = DhcpServer("kea://ctrl01/")
    sent = []

    def command(cmd, arguments=None):
        sent.append((cmd, arguments))
        return {"result": 0}

    server.command = command
    server.complete_target(_ctx(engine))
    return sent


def test_kea_default_completion_deletes_the_reservation():
    assert [cmd for cmd, _ in _kea_sent(_engine())] == ["reservation-del"]


def test_kea_keep_upserts_the_reservation_without_boot_fields():
    # reservation-add upserts, so the old reservation survives a failure rather
    # than the target being left with none.
    sent = _kea_sent(_engine({"dhcp_complete": SANBOOT}))
    assert [cmd for cmd, _ in sent] == ["reservation-add"]
    reservation = dict(sent[0][1])["reservation"]
    assert reservation["boot-file-name"] == "sanboot.ipxe"
    names = [o.get("name") for o in reservation.get("option-data", [])]
    assert "tftp-server-name" not in names


def test_kea_keep_bare_has_no_boot_fields_at_all():
    sent = _kea_sent(_engine({"dhcp_complete": {"keep": True}}))
    reservation = dict(sent[0][1])["reservation"]
    assert "boot-file-name" not in reservation and "next-server" not in reservation


# -- dnsmasq ---------------------------------------------------------------


def test_dnsmasq_keep_rewrites_the_region(tmp_path):
    hosts = tmp_path / "h"
    hosts.mkdir()
    opts = tmp_path / "o"
    opts.mkdir()
    uri = f"dnsmasq:///?hostsfile={hosts.as_posix()}&optsfile={opts.as_posix()}"
    engine = _engine({"dhcp_complete": SANBOOT})
    server = DhcpServer(uri)
    server.add_target(_ctx(_engine()))
    before = "\n".join(p.read_text() for p in opts.iterdir())
    assert "undionly.kpxe" in before
    server.complete_target(_ctx(engine))
    after = "\n".join(p.read_text() for p in opts.iterdir())
    assert "sanboot.ipxe" in after and "undionly.kpxe" not in after
    # The host line stays, so the address is still reserved.
    assert any(p.read_text().strip() for p in hosts.iterdir())


# -- conditions interact with a kept entry --------------------------------


def test_a_kept_entry_loses_its_condition_membership():
    # Otherwise the condition keeps serving the installer to a finished machine,
    # which is this feature's own failure arriving through the other one.
    detached = []

    class keeps(DhcpServer):  # scheme `keeps://`
        def keep_target(self, ctx, completion):
            pass

        def remove_condition_member(self, ctx, condition):
            detached.append(condition.name)

    engine = _engine(
        {
            "dhcp_complete": SANBOOT,
            "dhcp_when": {
                "ipxe": {
                    "match": {"user-class": "iPXE"},
                    "options": {"boot-file-name": "boot.ipxe"},
                }
            },
        }
    )
    DhcpServer("keeps://host/").complete_target(_ctx(engine))
    assert detached == ["ipxe"]


def test_a_removed_entry_needs_no_membership_work():
    detached = []

    class removes(DhcpServer):  # scheme `removes://`
        def remove_target(self, ctx):
            pass

        def remove_condition_member(self, ctx, condition):
            detached.append(condition.name)

    engine = _engine(
        {
            "dhcp_when": {
                "ipxe": {
                    "match": {"user-class": "iPXE"},
                    "options": {"boot-file-name": "boot.ipxe"},
                }
            }
        }
    )
    DhcpServer("removes://host/").complete_target(_ctx(engine))
    assert detached == [], "deleting the entry drops the membership with it"


def test_a_backend_that_cannot_keep_says_so(caplog):
    class cannotkeep(DhcpServer):  # scheme `cannotkeep://`
        def remove_target(self, ctx):
            pass

    engine = _engine({"dhcp_complete": {"keep": True}})
    with pytest.raises(NotImplementedError, match="cannot keep a reservation"):
        DhcpServer("cannotkeep://host/").complete_target(_ctx(engine))


def test_the_engine_logs_a_failing_completion_and_carries_on(caplog):
    tried = []

    class refuses(DhcpServer):  # scheme `refuses://`
        def remove_target(self, ctx):
            raise RuntimeError("nope")

    class works(DhcpServer):  # scheme `works://`
        def remove_target(self, ctx):
            tried.append(self.uri)

    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": ["refuses://a", "works://b"],
            }
        }
    )
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.complete(engine.lookup_target("web01"))
    assert tried == ["works://b"]
    assert "could not disarm" in caplog.text
