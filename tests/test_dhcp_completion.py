"""`dhcp_complete:` -- what a finished target's entry becomes, as netboot layers it.

The failure this prevents: a machine that still receives `boot-file-name` after
its install finishes boots the installer again. Removing the reservation stops
that and throws away the fixed address; keeping it without boot options, or with
a `sanboot` script, stops it and does not. How each backend keeps an entry is
dhcpctl's, and tested there.
"""

import logging

import dhcpctl
import pytest

import netboot
from netboot.dhcp import build_completion, disarm, reservation_for


@pytest.fixture
def backend(request):
    """Register a backend class under its own name as scheme, unregistered after."""

    def register(cls):
        dhcpctl.register_backend(cls.__name__, cls)
        request.addfinalizer(lambda: dhcpctl.unregister_backend(cls.__name__))
        return cls

    return register


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


def test_removing_the_entry_is_the_default():
    completion = build_completion(_ctx())
    assert completion.keep is False
    assert not completion


def test_keep_without_options_drops_only_the_boot_options():
    ctx = _ctx(_engine({"dhcp_complete": {"keep": True}}))
    completion = build_completion(ctx)
    server = dhcpctl.connect("windhcp://dhcp01/")
    kept = completion.apply_to(reservation_for(ctx, server).options)
    assert not any(name in kept for name in dhcpctl.BOOT_OPTIONS)
    # The options that make a reservation worth keeping are still there.
    assert kept["router"] is not None and "subnet-mask" in kept


def test_keep_with_options_replaces_rather_than_merges():
    ctx = _ctx(_engine({"dhcp_complete": SANBOOT}))
    completion = build_completion(ctx)
    server = dhcpctl.connect("windhcp://dhcp01/")
    kept = completion.apply_to(reservation_for(ctx, server).options)
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


# -- conditions interact with a kept entry --------------------------------


def test_a_kept_entry_loses_its_condition_membership(backend):
    # Otherwise the condition keeps serving the installer to a finished machine,
    # which is this feature's own failure arriving through the other one.
    detached = []

    @backend
    class keeps(dhcpctl.DHCPServer):
        def keep(self, reservation):
            pass

        def remove_condition_member(self, reservation, condition):
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
    disarm(_ctx(engine), dhcpctl.connect("keeps://host/"))
    assert detached == ["ipxe"]


def test_a_removed_entry_needs_no_membership_work(backend):
    detached = []

    @backend
    class removes(dhcpctl.DHCPServer):
        def remove(self, reservation):
            pass

        def remove_condition_member(self, reservation, condition):
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
    disarm(_ctx(engine), dhcpctl.connect("removes://host/"))
    assert detached == [], "deleting the entry drops the membership with it"


def test_a_backend_that_cannot_keep_says_so(caplog, backend):
    @backend
    class cannotkeep(dhcpctl.DHCPServer):
        def remove(self, reservation):
            pass

    engine = _engine({"dhcp_complete": {"keep": True}})
    with pytest.raises(NotImplementedError, match="cannot keep a reservation"):
        disarm(_ctx(engine), dhcpctl.connect("cannotkeep://host/"))


def test_the_engine_logs_a_failing_completion_and_carries_on(caplog, backend):
    tried = []

    @backend
    class refuses(dhcpctl.DHCPServer):
        def remove(self, reservation):
            raise RuntimeError("nope")

    @backend
    class works(dhcpctl.DHCPServer):
        def remove(self, reservation):
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
