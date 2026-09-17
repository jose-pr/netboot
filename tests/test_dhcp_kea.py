"""The Kea backend, against a stub control agent.

No test reaches a Kea: a fake `requests` module records what netboot posted and
answers with the shapes Kea really returns (`result` 0/1/2/3).
"""

import types

import pytest

import netboot
from netboot.dhcp import DhcpServer


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeRequests:
    """Stands in for the `requests` module, one queued reply per command."""

    def __init__(self, replies):
        self.replies = dict(replies)
        self.posted = []

    def post(self, url, json=None, auth=None, timeout=None):
        self.posted.append((url, json, auth))
        command = json["command"]
        reply = self.replies.get(command, {"result": 0, "text": "ok"})
        return _Response([reply] if isinstance(reply, dict) else reply)


@pytest.fixture
def kea_server(monkeypatch):
    def build(uri="kea://10.0.0.1:8000/", replies=None):
        server = DhcpServer(uri)
        fake = _FakeRequests(replies or {})
        monkeypatch.setattr(server, "_session", lambda: fake)
        return server, fake

    return build


def _engine(**overrides):
    config = {
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {
            "lan": {"network": "10.0.0.0/24", "gateway": "10.0.0.1", "subnet_id": 44}
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


def _ctx(engine):
    return engine.make_context(engine.lookup_target("web01"))


def test_add_posts_one_reservation_with_fields_and_options(kea_server):
    server, fake = kea_server("kea://10.0.0.1:8000/?domain-name-servers=10.0.0.53")
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {
                    "boot-file-name": "pxelinux.0",
                    "next-server": "10.0.0.2",
                },
            }
        }
    )
    server.add_target(_ctx(engine))

    assert len(fake.posted) == 1
    url, payload, _ = fake.posted[0]
    assert url == "http://10.0.0.1:8000/"
    assert payload["command"] == "reservation-add"
    assert payload["service"] == ["dhcp4"]
    reservation = payload["arguments"]["reservation"]
    assert reservation["subnet-id"] == 44
    assert reservation["hw-address"] == "aa:bb:cc:dd:ee:ff"
    assert reservation["ip-address"] == "10.0.0.10"
    # Reservation fields, not option-data entries.
    assert reservation["boot-file-name"] == "pxelinux.0"
    assert reservation["next-server"] == "10.0.0.2"
    assert {"name": "domain-name-servers", "data": "10.0.0.53"} in reservation[
        "option-data"
    ]
    assert not any(
        entry.get("name") in ("boot-file-name", "next-server")
        for entry in reservation["option-data"]
    )


def test_a_numeric_option_uses_its_code(kea_server):
    server, fake = kea_server("kea://10.0.0.1:8000/?option-252=http://proxy/wpad.dat")
    server.add_target(_ctx(_engine()))
    reservation = fake.posted[0][1]["arguments"]["reservation"]
    assert {"code": 252, "data": "http://proxy/wpad.dat"} in reservation["option-data"]


def test_remove_sends_reservation_del_and_accepts_not_found(kea_server):
    server, fake = kea_server(
        replies={"reservation-del": {"result": 3, "text": "none"}}
    )
    server.remove_target(_ctx(_engine()))
    payload = fake.posted[0][1]
    assert payload["command"] == "reservation-del"
    assert payload["arguments"] == {
        "subnet-id": 44,
        "identifier-type": "hw-address",
        "identifier": "aa:bb:cc:dd:ee:ff",
    }


def test_an_unsupported_result_explains_the_usual_cause(kea_server):
    server, _ = kea_server(
        replies={"reservation-add": {"result": 2, "text": "not supported"}}
    )
    with pytest.raises(netboot.PixieConfigError) as excinfo:
        server.add_target(_ctx(_engine()))
    message = str(excinfo.value)
    assert "not supported" in message
    assert "host_cmds" in message and "read-only" in message


def test_an_existing_reservation_is_refused_not_overwritten(kea_server):
    server, _ = kea_server(
        replies={
            "reservation-add": {"result": 1, "text": "database exists error"},
        }
    )
    with pytest.raises(netboot.PixieConfigError, match="will not"):
        server.add_target(_ctx(_engine()))


def test_the_subnet_id_is_discovered_from_the_zone_network(kea_server):
    server, fake = kea_server(
        replies={
            "config-get": {
                "result": 0,
                "arguments": {
                    "Dhcp4": {
                        "subnet4": [
                            {"id": 7, "subnet": "192.168.0.0/24"},
                            {"id": 8, "subnet": "10.0.0.0/24"},
                        ]
                    }
                },
            }
        }
    )
    engine = _engine(dhcpzones={"lan": {"network": "10.0.0.0/24"}})  # no subnet_id
    server.add_target(_ctx(engine))
    commands = [payload["command"] for _, payload, _ in fake.posted]
    assert commands == ["config-get", "reservation-add"]
    assert fake.posted[1][1]["arguments"]["reservation"]["subnet-id"] == 8

    # Resolved once per zone, then cached.
    server.add_target(_ctx(engine))
    assert [payload["command"] for _, payload, _ in fake.posted].count(
        "config-get"
    ) == 1


def test_an_unmatched_zone_refuses_rather_than_guessing(kea_server):
    server, _ = kea_server(
        replies={
            "config-get": {"result": 0, "arguments": {"Dhcp4": {"subnet4": []}}},
        }
    )
    engine = _engine(dhcpzones={"lan": {"network": "10.0.0.0/24"}})
    with pytest.raises(netboot.PixieConfigError, match="subnet_id"):
        server.add_target(_ctx(engine))


def test_a_target_without_a_mac_is_named(kea_server, monkeypatch):
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
    server, _ = kea_server()
    engine = _engine(
        targets={"nomac": {"ip": "10.0.0.11", "image": "debian", "dhcpzone": "lan"}}
    )
    ctx = engine.make_context(engine.lookup_target("nomac"))
    with pytest.raises(netboot.PixieLookupError, match="nomac"):
        server.add_target(ctx)


def test_https_and_auth_come_from_the_uri():
    server = DhcpServer("keas://admin:secret@kea.example:8443/")
    assert server.endpoint == "https://kea.example:8443/"
    assert server.auth == ("admin", "secret")


def test_without_requests_the_extra_is_named(monkeypatch):
    server = DhcpServer("kea://10.0.0.1:8000/")
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__
    )

    def no_requests(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("no requests here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(__import__("builtins").__dict__, "__import__", no_requests)
    try:
        with pytest.raises(ImportError, match=r"netboot\[kea\]"):
            server._session()
    finally:
        monkeypatch.undo()


def test_raw_entries_are_merged_as_option_data(kea_server):
    server, fake = kea_server(
        'kea://10.0.0.1:8000/?raw.kea=%7B"code"%3A%20250%2C%20"data"%3A%20"x"%7D'
    )
    # A raw kea entry is JSON text; netboot passes it through untouched.
    server.options.raw["kea"] = [{"code": 250, "data": "x"}]
    server.add_target(_ctx(_engine()))
    reservation = fake.posted[0][1]["arguments"]["reservation"]
    assert {"code": 250, "data": "x"} in reservation["option-data"]
