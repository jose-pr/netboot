"""The dnsmasq backend, against a real temporary tree and a recorded reload.

Nothing here talks to a dnsmasq: the files are the contract, so the tests read
them back whole rather than asserting on substrings.
"""

import logging

import pytest

import netboot
from netboot.dhcp import DhcpServer


@pytest.fixture
def engine_factory(tmp_path):
    def build(uri, **overrides):
        config = {
            "images": {"debian": {"template_path": []}},
            "dhcpzones": {
                "lan": {
                    "network": "10.0.0.0/24",
                    "gateway": "10.0.0.1",
                    "nameservers": ["10.0.0.53"],
                    "dhcpservers": [uri],
                }
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

    return build


def _dirs(tmp_path):
    hosts = tmp_path / "hosts.d"
    opts = tmp_path / "opts.d"
    hosts.mkdir()
    opts.mkdir()
    return hosts, opts


def _uri(hosts, opts, extra=""):
    return (
        f"dnsmasq:///?hostsfile={hosts.as_posix()}/&optsfile={opts.as_posix()}/{extra}"
    )


def test_directory_mode_writes_one_file_per_target(tmp_path, engine_factory, caplog):
    hosts, opts = _dirs(tmp_path)
    engine = engine_factory(
        _uri(hosts, opts, "&router=10.0.0.1"),
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {
                    "boot-file-name": "pxelinux.0",
                    "next-server": "10.0.0.2",
                },
            }
        },
    )
    with caplog.at_level(logging.INFO, logger="netboot"):
        engine.initialize(engine.lookup_target("web01"))

    host_file = hosts / "web01.conf"
    assert host_file.read_text(encoding="utf-8") == (
        "dhcp-host=aa:bb:cc:dd:ee:ff,set:web01,10.0.0.10,web01\n"
    )
    written = (opts / "web01.conf").read_text(encoding="utf-8").splitlines()
    assert "dhcp-boot=tag:web01,pxelinux.0,,10.0.0.2" in written
    assert "dhcp-option=tag:web01,3,10.0.0.1" in written  # router
    assert "dhcp-option=tag:web01,6,10.0.0.53" in written  # dns
    # No reload configured and a directory: nothing to run, said once.
    assert "dhcp-hostsdir" in caplog.text


def test_removal_unlinks_and_is_quiet_the_second_time(tmp_path, engine_factory):
    hosts, opts = _dirs(tmp_path)
    engine = engine_factory(_uri(hosts, opts))
    target = engine.lookup_target("web01")
    engine.initialize(target)
    assert (hosts / "web01.conf").exists()

    engine.complete(target)
    assert not (hosts / "web01.conf").exists()
    assert not (opts / "web01.conf").exists()
    engine.complete(target)  # must not raise


def test_file_mode_edits_only_its_own_region(tmp_path, engine_factory):
    hosts_file = tmp_path / "netboot.hosts"
    opts_file = tmp_path / "netboot.opts"
    hosts_file.write_text(
        "# written by hand\ndhcp-host=11:22:33:44:55:66,10.0.0.99,other\n# tail\n",
        encoding="utf-8",
    )
    opts_file.write_text("dhcp-option=99,hand-written\n", encoding="utf-8")
    uri = (
        f"dnsmasq:///?hostsfile={hosts_file.as_posix()}"
        f"&optsfile={opts_file.as_posix()}&reload=true"
    )
    engine = engine_factory(uri)
    target = engine.lookup_target("web01")

    engine.initialize(target)
    text = hosts_file.read_text(encoding="utf-8")
    assert (
        "# written by hand" in text and "10.0.0.99,other" in text and "# tail" in text
    )
    assert "# >>> netboot web01" in text and "set:web01" in text

    engine.complete(target)
    after = hosts_file.read_text(encoding="utf-8")
    assert "netboot web01" not in after and "set:web01" not in after
    # Every foreign line survived add + remove, in order.
    assert after.splitlines() == [
        "# written by hand",
        "dhcp-host=11:22:33:44:55:66,10.0.0.99,other",
        "# tail",
    ]


def test_file_mode_without_a_reload_refuses(tmp_path, engine_factory):
    hosts_file = tmp_path / "netboot.hosts"
    hosts_file.write_text("", encoding="utf-8")
    engine = engine_factory(f"dnsmasq:///?hostsfile={hosts_file.as_posix()}")
    with pytest.raises(netboot.PixieConfigError, match="reload"):
        engine.initialize(engine.lookup_target("web01"))


def test_a_remote_reload_runs_over_ssh(tmp_path, engine_factory, monkeypatch):
    # A URI with a host means remote paths. Stub the path seam so the test needs
    # no SSH server (and no sftp backend), and check what reload actually runs.
    hosts, opts = _dirs(tmp_path)
    from netboot.utils.misc import parse_path as real_parse_path

    def fake_parse_path(value):
        text = str(value)
        prefix = "sftp://admin@dhcp01"
        if text.startswith(prefix):
            return real_parse_path(text[len(prefix) :].lstrip("/"))
        return real_parse_path(text)

    monkeypatch.setattr("netboot.dhcp.dnsmasq.parse_path", fake_parse_path)
    # The remote seam is stubbed whole, so the "install the ssh extra" guard
    # (tested on its own below) must not fire here.
    monkeypatch.setattr("netboot.dhcp.dnsmasq._require_sftp", lambda uri: None)
    recorded = []
    monkeypatch.setattr(
        "netboot.dhcp.dnsmasq._subprocess.run",
        lambda argv, **kw: recorded.append(argv),
    )
    uri = (
        "dnsmasq://admin@dhcp01/?hostsfile=/"
        f"{hosts.as_posix()}/&optsfile=/{opts.as_posix()}/"
        "&reload=systemctl+reload+dnsmasq"
    )
    engine = engine_factory(uri)
    engine.initialize(engine.lookup_target("web01"))
    assert recorded == [["ssh", "admin@dhcp01", "systemctl", "reload", "dnsmasq"]]
    assert (hosts / "web01.conf").exists()


def test_a_remote_path_without_the_extra_names_it(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ImportError, match=r"netboot\[ssh\]"):
        DhcpServer("dnsmasq://dhcp01/etc/dnsmasq.d/hosts.d/")


def test_a_local_reload_runs_directly(tmp_path, engine_factory, monkeypatch):
    hosts, opts = _dirs(tmp_path)
    recorded = []
    monkeypatch.setattr(
        "netboot.dhcp.dnsmasq._subprocess.run",
        lambda argv, **kw: recorded.append(argv),
    )
    engine = engine_factory(_uri(hosts, opts, "&reload=systemctl+reload+dnsmasq"))
    engine.initialize(engine.lookup_target("web01"))
    assert recorded == [["systemctl", "reload", "dnsmasq"]]


def test_a_target_without_a_mac_is_named(tmp_path, engine_factory, monkeypatch):
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
    hosts, opts = _dirs(tmp_path)
    engine = engine_factory(
        _uri(hosts, opts),
        targets={"nomac": {"ip": "10.0.0.11", "image": "debian", "dhcpzone": "lan"}},
    )
    with pytest.raises(netboot.PixieLookupError, match="nomac"):
        engine.initialize(engine.lookup_target("nomac"))


def test_raw_lines_pass_through_untranslated(tmp_path, engine_factory):
    hosts, opts = _dirs(tmp_path)
    engine = engine_factory(
        _uri(
            hosts, opts, "&raw.dnsmasq=dhcp-option%3Dtag%3Aweb01%2C252%2Chttp%3A%2F%2Fx"
        )
    )
    engine.initialize(engine.lookup_target("web01"))
    assert (
        "dhcp-option=tag:web01,252,http://x"
        in (opts / "web01.conf").read_text(encoding="utf-8").splitlines()
    )


def test_an_apply_time_key_in_the_uri_is_refused(tmp_path, engine_factory):
    hosts, opts = _dirs(tmp_path)
    with pytest.raises(netboot.PixieConfigError, match="boot-file-name"):
        DhcpServer(_uri(hosts, opts, "&boot-file-name=pxelinux.0"))
