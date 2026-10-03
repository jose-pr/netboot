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
    # A one-element list: -Value takes String[], never a joined string.
    assert ids[67] == ["boot\\x64\\wdsnbp.com"]  # boot file
    assert ids[66] == ["10.0.0.2"]  # tftp server
    assert ids[6] == ["10.0.0.53"]
    assert ids[3] == ["10.0.0.1"]  # router, from the zone


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


# -- method=netsh -----------------------------------------------------------


def _commands(script: str) -> "list[list[str]]":
    """The netsh argument lists the script would run, in order."""
    return [c["Args"] for c in _payload(script)["Commands"]]


def test_the_default_method_is_the_powershell_cmdlets(stub):
    server, ran = stub()
    assert server.method == "powershell"
    engine = _engine()
    server.add_target(_ctx(engine))
    assert "Add-DhcpServerv4Reservation" in ran[0]
    assert "netsh" not in ran[0]


def test_an_unknown_method_is_refused():
    with pytest.raises(netboot.PixieConfigError, match="method must be"):
        DhcpServer("windhcp://dhcp01/?method=wmic")


def test_netsh_adds_the_reservation_then_its_options(stub):
    server, ran = stub("windhcp://admin@dhcp01/?method=netsh")
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "pxelinux.0", "lease-time": 600},
            }
        }
    )
    server.add_target(_ctx(engine))
    commands = _commands(ran[0])
    # The reservation first -- an option on a reservation that does not exist
    # yet is an error, so order is part of the contract.
    assert commands[0] == [
        "dhcp",
        "server",
        "scope",
        "10.0.0.0",
        "add",
        "reservedip",
        "10.0.0.10",
        "aabbccddeeff",  # netsh wants bare hex, not the cmdlets' hyphens
        "web01",
        "netboot",
        "BOTH",
    ]
    rest = {tuple(c[4:]) for c in commands[1:]}
    assert (
        "set",
        "reservedoptionvalue",
        "10.0.0.10",
        "3",
        "IPADDRESS",
        "10.0.0.1",
    ) in rest
    assert (
        "set",
        "reservedoptionvalue",
        "10.0.0.10",
        "67",
        "STRING",
        "pxelinux.0",
    ) in rest
    assert ("set", "reservedoptionvalue", "10.0.0.10", "51", "DWORD", "600") in rest


def test_netsh_passes_a_multi_valued_option_as_separate_arguments(stub):
    # Regression: the cmdlets take one comma-joined string, netsh takes one
    # argument per value. Joining for both sent "a,b" as a single address.
    server, ran = stub("windhcp://dhcp01/?method=netsh")
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "nameservers": ["10.0.0.53", "10.0.0.54"],
            }
        }
    )
    server.add_target(_ctx(engine))
    dns = [c for c in _commands(ran[0]) if "6" in c and "IPADDRESS" in c][0]
    assert dns[-2:] == ["10.0.0.53", "10.0.0.54"]


def test_the_cmdlets_get_a_list_of_values_not_a_joined_string(stub):
    # Measured against Windows Server 2025: `Set-DhcpServerv4OptionValue -Value`
    # takes String[] and refuses "a,b" with "Parameters for option value ... do
    # not match with option definition", which under ErrorAction Stop also
    # abandoned every option after it. A reservation then had a router and no
    # boot file, which is a target that gets an address and cannot boot.
    server, ran = stub("windhcp://dhcp01/")
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "nameservers": ["10.0.0.53", "10.0.0.54"],
            }
        }
    )
    server.add_target(_ctx(engine))
    options = {o["Id"]: o["Value"] for o in _payload(ran[0])["Options"]}
    assert options[6] == ["10.0.0.53", "10.0.0.54"]
    # A single-valued option is still a one-element list, so the script needs no
    # special case.
    assert options[3] == ["10.0.0.1"]


def test_netsh_addresses_a_remote_server_unc_style(stub):
    server, ran = stub("windhcp://dhcp01/?method=netsh&server=dhcp02")
    server.add_target(_ctx(_engine()))
    assert _commands(ran[0])[0][:3] == ["dhcp", "server", "\\\\dhcp02"]


def test_a_server_already_written_unc_style_is_not_doubled(stub):
    server, ran = stub("windhcp://dhcp01/?method=netsh&server=" + "%5C%5Cdhcp02")
    assert server.server == "\\\\dhcp02"
    server.add_target(_ctx(_engine()))
    assert _commands(ran[0])[0][2] == "\\\\dhcp02"


def test_netsh_removal_tolerates_a_missing_reservation(stub):
    server, ran = stub("windhcp://dhcp01/?method=netsh")
    server.remove_target(_ctx(_engine()))
    payload = _payload(ran[0])
    assert payload["Commands"][0]["Args"][4:] == [
        "delete",
        "reservedip",
        "10.0.0.10",
        "aabbccddeeff",
    ]
    # Cleanup re-runs, so deleting nothing is success.
    assert payload["Commands"][0]["Ignore"] is True


def test_netsh_arguments_are_passed_as_an_array_not_a_command_line(stub):
    # The whole reason netsh is driven from PowerShell: an argument stays an
    # array element, so no value can become syntax.
    server, ran = stub("windhcp://dhcp01/?method=netsh")
    engine = _engine(
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
                "globals": {},
            }
        }
    )
    server.add_target(_ctx(engine))
    # `@a` on a variable splats; `@($c.Args)` would pass one array argument.
    assert "$a = @($c.Args)" in ran[0] and "& netsh @a" in ran[0]
    # Nothing is spliced into the script: the only quote-bearing text is JSON.
    assert "$LASTEXITCODE" in ran[0]


def test_a_hostile_value_stays_inside_the_json_payload(stub):
    server, ran = stub("windhcp://dhcp01/?method=netsh")
    engine = _engine(
        targets={
            "web01'; Remove-Item C:\\ -Recurse #": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        }
    )
    server.add_target(_ctx(engine, "web01'; Remove-Item C:\\ -Recurse #"))
    script = ran[0]
    # It survives as data, exactly as given, and never as script text.
    assert _commands(script)[0][-3] == "web01'; Remove-Item C:\\ -Recurse #"
    assert "Remove-Item C:\\ -Recurse #'" not in script.split("ConvertFrom-Json")[0]


def test_netsh_keeps_the_raw_escape_hatch(stub):
    server, ran = stub(
        "windhcp://dhcp01/?method=netsh&raw.windhcp=Write-Output+'extra'"
    )
    server.add_target(_ctx(_engine()))
    assert "Write-Output 'extra'" in ran[0]


def test_netsh_asks_the_server_to_confirm_what_it_applied(stub):
    # netsh exits 0 and says "Command completed successfully" having dropped a
    # value it disliked (measured: option 6 with an unreachable name server), so
    # the payload carries what to verify and where to read it back from.
    server, ran = stub("windhcp://dhcp01/?method=netsh&server=dhcp02")
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "nameservers": ["10.0.0.53", "10.0.0.54"],
            }
        }
    )
    server.add_target(_ctx(engine))
    payload = _payload(ran[0])
    assert payload["IPAddress"] == "10.0.0.10"
    assert payload["VerifyArgs"][-1] == "dump"
    assert payload["VerifyArgs"][:3] == ["dhcp", "server", "\\\\dhcp02"]
    counts = {v["Id"]: v["Count"] for v in payload["Verify"]}
    assert counts["6"] == 2  # two name servers must survive, not one
    assert counts["3"] == 1


def test_the_removal_script_has_nothing_to_verify(stub):
    server, ran = stub("windhcp://dhcp01/?method=netsh")
    server.remove_target(_ctx(_engine()))
    assert "Verify" not in _payload(ran[0])


# -- transport=local --------------------------------------------------------


def test_an_empty_host_means_the_shell_is_here():
    # `windhcp:///?server=dhcp01` is the ordinary RSAT shape: run PowerShell
    # here, act on that server. There is no host to reach, so none is dialled.
    server = DhcpServer("windhcp:///?server=dhcp01")
    assert server.transport == "local"
    assert server.server == "dhcp01"
    assert server.port == 0


def test_a_named_host_still_uses_a_transport():
    # Guessing that `windhcp://dhcp01/` is local because dhcp01 happens to be
    # this machine would surprise whoever wrote the name.
    assert DhcpServer("windhcp://dhcp01/").transport == "ssh"


def test_local_runs_the_same_script_the_other_transports_send(monkeypatch):
    import subprocess

    calls = []

    class _Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    engine = _engine()
    ctx = _ctx(engine)

    local = DhcpServer("windhcp:///?server=dhcp01")
    local.add_target(ctx)
    argv, script = calls[-1]
    assert argv[0] == "powershell" and argv[-2:] == ["-Command", "-"]
    assert "-NonInteractive" in argv

    # Byte-identical to what ssh would have piped in.
    over_ssh = DhcpServer("windhcp://host/?server=dhcp01")
    sent = []
    over_ssh.run = lambda payload, body: sent.append(over_ssh.script_for(payload, body))
    over_ssh.add_target(ctx)
    assert script in sent


def test_a_failing_local_powershell_is_reported(monkeypatch):
    import subprocess

    class _Failed:
        returncode = 3
        stdout = ""
        stderr = "it did not work"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Failed())
    server = DhcpServer("windhcp:///?server=dhcp01")
    with pytest.raises(netboot.PixieConfigError, match="local PowerShell failed"):
        server.add_target(_ctx(_engine()))


def test_an_unknown_transport_is_refused():
    with pytest.raises(netboot.PixieConfigError, match="local, ssh or winrm"):
        DhcpServer("windhcp://h/?transport=telnet")


def test_local_is_refused_where_there_is_no_powershell(monkeypatch):
    import netboot.dhcp.windhcp as mod

    monkeypatch.setattr(mod._os, "name", "posix")
    with pytest.raises(netboot.PixieConfigError, match="needs Windows PowerShell"):
        DhcpServer("windhcp:///?server=dhcp01")
