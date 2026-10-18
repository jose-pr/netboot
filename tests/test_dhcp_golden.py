"""What every DHCP backend sends to its server, frozen.

Each scenario arms and then completes one target through netboot's public path
(`Pixie.initialize` / `Pixie.complete`, and `pixie dhcp-config` for recipes),
and records exactly what would reach the server: the files dnsmasq reads, the
commands and scripts run, the Kea control-agent payloads, the OMAPI calls and
the WinRM scripts. The fakes sit at the lowest seams (`subprocess.run`,
`requests.post`, the `pypureomapi` and `winrm` modules), so the record does not
depend on how the backends are organised.

Regenerate after an intended change with `NETBOOT_UPDATE_GOLDEN=1 pytest
tests/test_dhcp_golden.py` and review the diff.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import types

import dhcpctl
import pytest

import netboot

pytest.importorskip("requests", reason="needs the 'kea' extra")

GOLDEN = pathlib.Path(__file__).parent / "golden" / "dhcp"
#: The dhcpd scenarios' OMAPI secret.
OMAPI_KEY = "c2VjcmV0"
UPDATE = bool(os.environ.get("NETBOOT_UPDATE_GOLDEN"))


class Recorder:
    """Collects what reached a server, with temporary paths made stable."""

    def __init__(self, tmp: pathlib.Path) -> None:
        self.tmp = tmp
        self.lines: list = []

    def text(self, value) -> str:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, sort_keys=True, default=repr)
        )
        for spelling in {str(self.tmp), self.tmp.as_posix()}:
            text = text.replace(spelling, "<tmp>")
        return text.replace("\\", "/")

    def add(self, kind: str, value) -> None:
        self.lines.append(f"--- {kind}\n{self.text(value)}")

    def files(self, label: str) -> None:
        root = self.tmp / "dnsmasq"
        if not root.exists():
            return
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(self.tmp).as_posix()
            self.add(f"file {label} {rel}", path.read_text(encoding="utf-8"))


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    rec = Recorder(tmp_path)

    def fake_run(argv, *args, **kwargs):
        rec.add("subprocess", {"argv": list(argv), "input": kwargs.get("input")})
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # A dnsmasq directory needs SIGHUP once an entry is taken away; this host
    # has no dnsmasq to signal, and Windows has no SIGHUP.
    monkeypatch.setattr(
        "dhcpctl.backends._dnsmasq._signal_pidfile",
        lambda pidfile: rec.add("sighup", pidfile),
    )

    import requests

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    def fake_post(url, json=None, auth=None, timeout=None, **kwargs):
        rec.add("kea", {"url": url, "payload": json, "auth": auth})
        command = (json or {}).get("command")
        if command == "config-get":
            body = {
                "result": 0,
                "arguments": {
                    "Dhcp4": {"subnet4": [{"id": 7, "subnet": "10.0.0.0/24"}]}
                },
            }
        elif command == "class-get":
            body = {"result": 3, "text": "not found"}
        else:
            body = {"result": 0}
        return Response([body])

    monkeypatch.setattr(requests, "post", fake_post)

    UPDATE, STATUS = 3, 4

    class OmapiMessage:
        def __init__(self, kind):
            self.kind, self.message, self.obj = kind, [], []

        @classmethod
        def open(cls, kind):
            return cls(kind)

    def readable(value: bytes) -> str:
        text = value.decode("utf-8", "replace")
        return text if text.isprintable() else value.hex()

    class Omapi:
        def __init__(self, host, port, username, key, timeout=None):
            rec.add("omapi", ["connect", host, port, username, key])

        def query_server(self, message):
            rec.add(
                "omapi",
                [
                    "query",
                    message.kind.decode(),
                    [[k.decode(), readable(v)] for k, v in message.message],
                    [[k.decode(), readable(v)] for k, v in message.obj],
                ],
            )
            # A fresh server: opening without `create` finds nothing.
            created = any(k == b"create" for k, _ in message.message)
            return types.SimpleNamespace(opcode=UPDATE if created else STATUS)

        def __getattr__(self, name):
            def call(*args, **kwargs):
                rec.add("omapi", [name, list(args), kwargs])

            return call

    monkeypatch.setitem(
        sys.modules,
        "pypureomapi",
        types.SimpleNamespace(
            Omapi=Omapi,
            OmapiMessage=OmapiMessage,
            OMAPI_OP_UPDATE=UPDATE,
            pack_mac=lambda mac: mac.encode(),
            pack_ip=lambda ip: ip.encode(),
        ),
    )

    class Session:
        def __init__(self, endpoint, auth=None, transport=None):
            rec.add(
                "winrm", {"endpoint": endpoint, "auth": auth, "transport": transport}
            )

        def run_ps(self, script):
            rec.add("winrm-script", script)
            return types.SimpleNamespace(
                status_code=0, std_out=b'{"ok": true}', std_err=b""
            )

    monkeypatch.setitem(sys.modules, "winrm", types.SimpleNamespace(Session=Session))
    return rec


IPXE = {
    "ipxe": {
        "match": {"user-class": "iPXE"},
        "options": {"boot-file-name": "boot.ipxe"},
    }
}
KEEP = {"keep": True, "options": {"boot-file-name": "sanboot.ipxe"}}


def _config(server, *, when=None, complete=None, zone=None) -> dict:
    image = {
        "template_path": [],
        "dhcp_options": {"boot-file-name": "undionly.kpxe", "next-server": "10.0.0.2"},
    }
    if when:
        image["dhcp_when"] = when
    if complete:
        image["dhcp_complete"] = complete
    return {
        "images": {"debian": image},
        "dhcpzones": {
            "lan": {
                "network": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "nameservers": ["10.0.0.53", "10.0.0.54"],
                "domain": "example.com",
                "search": ["example.com"],
                "dhcpservers": [server],
                **(zone or {}),
            }
        },
        "targets": {
            "web01": {
                "mac": "aa:bb:cc:dd:ee:ff",
                "ip": "10.0.0.10",
                "hostname": "web01",
                "image": "debian",
            }
        },
    }


def _scenarios(tmp: pathlib.Path) -> dict:
    # Directory mode needs the directories to exist: a trailing slash on a path
    # that does not exist yet is not kept.
    for sub in ("hosts", "opts"):
        (tmp / "dnsmasq" / sub).mkdir(parents=True, exist_ok=True)
    hosts = (tmp / "dnsmasq" / "hosts").as_posix() + "/"
    opts = (tmp / "dnsmasq" / "opts").as_posix() + "/"
    hostsfile = (tmp / "dnsmasq" / "hosts.conf").as_posix()
    optsfile = (tmp / "dnsmasq" / "opts.conf").as_posix()
    win = "windhcp://admin@dhcp01/?server=dhcp01"
    return {
        "dnsmasq-dir": _config(f"dnsmasq:///?hostsfile={hosts}&optsfile={opts}"),
        "dnsmasq-dir-keep": _config(
            f"dnsmasq:///?hostsfile={hosts}&optsfile={opts}", complete=KEEP
        ),
        "dnsmasq-file": _config(
            f"dnsmasq:///?hostsfile={hostsfile}&optsfile={optsfile}"
            "&reload=systemctl%20reload%20dnsmasq"
        ),
        "kea": _config("kea://kea01:8000/?service=dhcp4", when=IPXE),
        "kea-keep": _config(
            "kea://admin:pw@kea01/", complete=KEEP, zone={"subnet_id": 3}
        ),
        "keas": _config("keas://kea01/"),
        # A server's secrets are its config entry's.
        "dhcpd-if": _config(
            {"uri": "dhcpd://dhcp01:7911/?keyname=omapi_key", "omapi_key": OMAPI_KEY},
            when=IPXE,
        ),
        "dhcpd-group": _config(
            {
                "uri": "dhcpd://dhcp01/?keyname=omapi_key&conditions=group",
                "omapi_key": OMAPI_KEY,
            },
            when=IPXE,
            complete=KEEP,
        ),
        "dhcpd-raw": _config({"uri": "dhcpd://dhcp01/", "omapi_key": OMAPI_KEY}),
        "windhcp-ssh": _config(win, when=IPXE),
        "windhcp-ssh-keep": _config(win, when=IPXE, complete=KEEP),
        "windhcp-shared": _config(win + "&conditions=shared", when=IPXE),
        "windhcp-netsh": _config(win + "&method=netsh", zone={"scope": "10.0.0.0"}),
        "windhcp-winrm": _config(
            {"uri": "windhcp://svc@dhcp01/?transport=winrm", "password": "pw"}
        ),
        "windhcp-local": _config("windhcp:///?server=dhcp01&transport=local"),
    }


#: Backend-native fragments a scenario's backend adds, by phase, through an
#: `extras()` override: the one way in for text no option models.
EXTRAS = {
    "dnsmasq-dir": ("dnsmasq", {"add": ["tag:web01,42,10.0.0.9"]}),
    "keas": ("keas", {"add": ['{"code": 42}']}),
    "dhcpd-raw": ("dhcpd", {"add": ['ddns-hostname "web01";'], "remove": ["# gone"]}),
    "windhcp-winrm": ("windhcp", {"add": ["Write-Verbose extra"]}),
}


@pytest.fixture
def extras_for(request):
    """Serve `scheme` with its backend plus `extras`, for one test."""

    def install(scheme, extras):
        backend = dhcpctl.get_backend(scheme)

        def with_extras(self, reservation, phase="add"):
            return backend.extras(self, reservation, phase) + list(
                extras.get(phase, ())
            )

        dhcpctl.register_backend(
            scheme,
            type(backend.__name__, (backend,), {"extras": with_extras}),
            replace=True,
        )
        request.addfinalizer(
            lambda: dhcpctl.register_backend(scheme, backend, replace=True)
        )

    return install


NAMES = (
    "dhcpd-group",
    "dhcpd-if",
    "dhcpd-raw",
    "dnsmasq-dir",
    "dnsmasq-dir-keep",
    "dnsmasq-file",
    "kea",
    "kea-keep",
    "keas",
    "windhcp-local",
    "windhcp-netsh",
    "windhcp-shared",
    "windhcp-ssh",
    "windhcp-ssh-keep",
    "windhcp-winrm",
)


def _run(name: str, config: dict, rec: Recorder, tmp: pathlib.Path) -> str:
    pixie = netboot.Pixie(**config)
    target = pixie.lookup_target("web01")
    rec.add("step", "initialize")
    pixie.initialize(target)
    rec.files("after-initialize")
    rec.add("step", "complete")
    pixie.complete(target)
    rec.files("after-complete")
    if any(
        "dhcp_when" in image for image in config["images"].values()
    ) and not name.startswith("dnsmasq"):
        recipes = []
        ctx = pixie.make_context(target)
        from netboot.cmds import dhcp_config

        for server in ctx.dhcpzone.dhcpservers:
            for condition in dhcp_config.conditions_for(ctx).values():
                recipes.append(dhcp_config.recipe(ctx, server, condition))
        rec.add("recipes", "\n\n".join(recipes))
    return "\n".join(rec.lines) + "\n"


@pytest.mark.skipif(os.name != "nt", reason="windhcp transport=local is Windows only")
@pytest.mark.parametrize("name", NAMES)
def test_what_each_backend_sends(name, recorder, tmp_path, extras_for):
    if name in EXTRAS:
        extras_for(*EXTRAS[name])
    scenarios = _scenarios(tmp_path)
    assert sorted(scenarios) == list(NAMES)
    config = scenarios[name]
    produced = _run(name, config, recorder, tmp_path)
    golden = GOLDEN / f"{name}.txt"
    if UPDATE:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(produced.encode("utf-8"))
    assert golden.read_text(encoding="utf-8") == produced
