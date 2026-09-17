"""The Windows DHCP backend, against a stub transport.

The script is the contract here, so the tests read the generated PowerShell and
the JSON payload inside it — including with values that try to break out of it.
"""

import json

import pytest

import netboot
from netboot.dhcp import DhcpServer


@pytest.fixture
def stub(monkeypatch):
    """A backend whose transport records the script instead of running it."""

    def build(uri="windhcp://admin@dhcp01/"):
        server = DhcpServer(uri)
        ran = []
        monkeypatch.setattr(
            server,
            "run",
            lambda payload, body: ran.append(server.script_for(payload, body)),
        )
        return server, ran

    return build


def _engine(**overrides):
    config = {
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24", "gateway": "10.0.0.1"}},
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


def _ctx(engine, target="web01"):
    return engine.make_context(engine.lookup_target(target))


def _payload(script: str) -> dict:
    """Parse back what PowerShell would parse: the single-quoted JSON literal."""
    start = script.index("ConvertFrom-Json '") + len("ConvertFrom-Json '")
    end = script.index("'\n", start)
    return json.loads(script[start:end].replace("''", "'"))


def test_add_builds_the_reservation_and_its_options(stub):
    server, ran = stub("windhcp://admin@dhcp01/?domain-name-servers=10.0.0.53")
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {
                    "boot-file-name": "boot\\x64\\wdsnbp.com",
                    "tftp-server-name": "10.0.0.2",
                },
            }
        }
    )
    server.add_target(_ctx(engine))

    script = ran[0]
    assert "Add-DhcpServerv4Reservation" in script
    assert "Set-DhcpServerv4OptionValue" in script
    payload = _payload(script)
    assert payload["ScopeId"] == "10.0.0.0"  # from the zone, at apply time
    assert payload["IPAddress"] == "10.0.0.10"
    assert payload["ClientId"] == "aa-bb-cc-dd-ee-ff"  # Windows' hyphen spelling
    assert payload["Name"] == "web01"
    ids = {option["Id"]: option["Value"] for option in payload["Options"]}
    assert ids[67] == "boot\\x64\\wdsnbp.com"  # boot file
    assert ids[66] == "10.0.0.2"  # tftp server
    assert ids[6] == "10.0.0.53"
    assert ids[3] == "10.0.0.1"  # router, from the zone


def test_computer_name_is_only_sent_when_it_differs(stub):
    server, ran = stub("windhcp://admin@jump/?server=dhcp01")
    server.add_target(_ctx(_engine()))
    assert _payload(ran[0])["ComputerName"] == "dhcp01"

    plain, ran_plain = stub("windhcp://admin@dhcp01/")
    plain.add_target(_ctx(_engine()))
    assert _payload(ran_plain[0])["ComputerName"] == ""


@pytest.mark.parametrize(
    "hostile",
    [
        "'; Remove-Item C:\\ -Recurse; #",
        "quote'inside",
        'double"quote',
        "line\nbreak",
    ],
)
def test_a_hostile_value_stays_data(stub, hostile):
    # The whole design: values are parsed as JSON by PowerShell, never spliced
    # into the script, so a value cannot become a statement.
    server, ran = stub()
    engine = _engine(
        targets={
            "web01": {
                "hostname": hostile,
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        }
    )
    server.add_target(_ctx(engine))
    script = ran[0]

    payload = _payload(script)
    assert payload["Name"] == "web01"
    # Every single quote inside the literal is doubled -- the one escaping rule.
    literal = script[
        script.index("ConvertFrom-Json '")
        + len("ConvertFrom-Json '") : script.index("'\n")
    ]
    assert "'" not in literal.replace("''", "")
    # And the script still has exactly one JSON literal to parse.
    assert script.count("ConvertFrom-Json") == 1


def test_remove_checks_before_removing(stub):
    server, ran = stub()
    server.remove_target(_ctx(_engine()))
    script = ran[0]
    assert "Get-DhcpServerv4Reservation" in script
    assert "Remove-DhcpServerv4Reservation" in script
    payload = _payload(script)
    assert payload["ClientId"] == "aa-bb-cc-dd-ee-ff"
    assert "IPAddress" not in payload


def test_the_zone_can_override_the_scope(stub):
    server, ran = stub()
    engine = _engine(
        dhcpzones={"lan": {"network": "10.0.0.0/24", "scope": "10.99.0.0"}}
    )
    server.add_target(_ctx(engine))
    assert _payload(ran[0])["ScopeId"] == "10.99.0.0"


def test_ssh_is_the_default_transport_and_builds_its_argv(monkeypatch):
    server = DhcpServer("windhcp://admin@dhcp01/?port=2222")
    assert server.transport == "ssh"
    recorded = {}

    class _Completed:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def fake_run(argv, input=None, **kwargs):
        recorded["argv"] = argv
        recorded["input"] = input
        return _Completed()

    monkeypatch.setattr("netboot.dhcp.windhcp._subprocess.run", fake_run)
    server.add_target(_ctx(_engine()))
    assert recorded["argv"][:4] == ["ssh", "-p", "2222", "admin@dhcp01"]
    assert recorded["argv"][4:] == [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "-",
    ]
    assert "Add-DhcpServerv4Reservation" in recorded["input"]


def test_a_failing_ssh_run_raises_with_the_error(monkeypatch):
    server = DhcpServer("windhcp://admin@dhcp01/")

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "Add-DhcpServerv4Reservation : Access is denied"

    monkeypatch.setattr(
        "netboot.dhcp.windhcp._subprocess.run", lambda *a, **k: _Failed()
    )
    with pytest.raises(netboot.PixieConfigError, match="Access is denied"):
        server.add_target(_ctx(_engine()))


def test_winrm_without_the_extra_names_it(monkeypatch):
    server = DhcpServer("windhcp://admin@dhcp01/?transport=winrm")
    real_import = __import__("builtins").__import__

    def no_winrm(name, *args, **kwargs):
        if name == "winrm":
            raise ImportError("no winrm here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(__import__("builtins").__dict__, "__import__", no_winrm)
    try:
        with pytest.raises(ImportError, match=r"netboot\[winrm\]"):
            server.add_target(_ctx(_engine()))
    finally:
        monkeypatch.undo()


def test_an_unknown_transport_is_refused():
    with pytest.raises(netboot.PixieConfigError, match="transport"):
        DhcpServer("windhcp://admin@dhcp01/?transport=telnet")


def test_a_target_without_a_mac_is_named(stub, monkeypatch):
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
    server, _ = stub()
    engine = _engine(
        targets={"nomac": {"ip": "10.0.0.11", "image": "debian", "dhcpzone": "lan"}}
    )
    with pytest.raises(netboot.PixieLookupError, match="nomac"):
        server.add_target(_ctx(engine, "nomac"))
