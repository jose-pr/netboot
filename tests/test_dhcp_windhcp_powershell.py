"""Executes the generated windhcp script in a real PowerShell, with netsh shimmed.

Every other windhcp test reads the script as text, which cannot catch a script
that does not do what it appears to: `& netsh @($c.Args)` *looks* like splatting
and is an array subexpression, so netsh received one argument instead of twelve.
Running it is the only way to see that, so this module does.

Skips where PowerShell is absent (Linux and macOS CI); the DHCP commands
themselves are never run -- `netsh` is a function that records its arguments.
"""

import json
import os
import shutil
import subprocess

import pytest

import netboot
from netboot.dhcp import DhcpServer

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="needs PowerShell")

#: Replaces netsh with a function that logs its arguments one per line and
#: returns `$env:NETSH_FAIL_AT`'s call as a failure, then runs the real script.
HARNESS = """param([string]$Script)
$global:calls = 0
function netsh {
    $i = $global:calls
    $global:calls++
    Add-Content -LiteralPath $env:NETSH_LOG -Value ("CALL " + ($args -join "|"))
    if ("$env:NETSH_FAIL_AT" -ne "" -and [int]$env:NETSH_FAIL_AT -eq $i) {
        $global:LASTEXITCODE = 1
    } else {
        $global:LASTEXITCODE = 0
    }
    "netsh output"
}
try {
    & ([scriptblock]::Create((Get-Content -Raw -LiteralPath $Script))) | Out-Null
    Write-Output "OK"
} catch {
    Write-Output ("THREW " + ($_.Exception.Message -replace '\\s+', ' '))
}
"""


def _engine(**overrides):
    config = {
        "images": {
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "pxelinux.0"},
            }
        },
        "dhcpzones": {
            "lan": {
                "network": "10.0.0.0/24",
                "nameservers": ["10.0.0.53", "10.0.0.54"],
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


def _script(uri, action="add", **overrides):
    server = DhcpServer(uri)
    captured = []
    server.run = lambda payload, body: captured.append(server.script_for(payload, body))
    engine = _engine(**overrides)
    ctx = engine.make_context(engine.lookup_target("web01"))
    getattr(server, f"{action}_target")(ctx)
    return captured[0]


def _run(tmp_path, script, fail_at=None):
    """Run `script` under the harness; return (verdict, [netsh arg lists])."""
    script_path = tmp_path / "script.ps1"
    # write_bytes, not write_text(newline=...): that keyword is 3.10+ and the
    # project floor is 3.9.
    script_path.write_bytes(script.encode("utf-8"))
    harness = tmp_path / "harness.ps1"
    harness.write_bytes(HARNESS.encode("utf-8"))
    log = tmp_path / "netsh.log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ, NETSH_LOG=str(log))
    env["NETSH_FAIL_AT"] = "" if fail_at is None else str(fail_at)
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-Script",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    calls = [
        line[len("CALL ") :].split("|")
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("CALL ")
    ]
    return completed.stdout.strip().splitlines()[-1], calls


def test_the_generated_script_runs_and_passes_each_argument_separately(tmp_path):
    # The regression this module exists for: one argument per element, so a
    # multi-valued option really is two addresses and not one token.
    verdict, calls = _run(tmp_path, _script("windhcp://dhcp01/?method=netsh"))
    assert verdict == "OK"
    assert calls[0][:6] == [
        "dhcp",
        "server",
        "scope",
        "10.0.0.0",
        "add",
        "reservedip",
    ]
    dns = [c for c in calls if "IPADDRESS" in c and "6" in c][0]
    assert dns[-2:] == ["10.0.0.53", "10.0.0.54"]


def test_a_remote_server_reaches_netsh_as_one_unc_argument(tmp_path):
    verdict, calls = _run(
        tmp_path, _script("windhcp://dhcp01/?method=netsh&server=dhcp02")
    )
    assert verdict == "OK"
    assert calls[0][2] == "\\\\dhcp02"


def test_a_failing_netsh_call_aborts_the_run_naming_the_command(tmp_path):
    verdict, calls = _run(
        tmp_path, _script("windhcp://dhcp01/?method=netsh"), fail_at=1
    )
    assert verdict.startswith("THREW")
    assert "set reservedoptionvalue" in verdict and "failed (1)" in verdict
    # It stops there rather than carrying on with a half-applied reservation.
    assert len(calls) == 2


def test_a_failing_delete_is_tolerated_so_cleanup_can_rerun(tmp_path):
    verdict, calls = _run(
        tmp_path,
        _script("windhcp://dhcp01/?method=netsh", action="remove"),
        fail_at=0,
    )
    assert verdict == "OK"
    assert calls[0][4:6] == ["delete", "reservedip"]


def test_a_hostile_value_arrives_as_one_argument_and_runs_nothing(tmp_path):
    # The payload is data: a value that looks like PowerShell must come out of
    # ConvertFrom-Json intact and reach netsh as a single argument.
    hostile = "web01'; Remove-Item C:\\ -Recurse #"
    script = _script(
        "windhcp://dhcp01/?method=netsh",
        targets={
            hostile: {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    )
    # `web01` is looked up by hostname, so the hostile id still resolves.
    verdict, calls = _run(tmp_path, script)
    assert verdict == "OK"
    assert hostile in calls[0]


def test_the_cmdlet_script_also_parses_and_runs(tmp_path):
    # Same check for the default method: the cmdlets are shimmed away, so this
    # is about the script being valid PowerShell and the payload parsing.
    script = _script("windhcp://dhcp01/")
    harness = (
        "function Add-DhcpServerv4Reservation { }\n"
        "function Set-DhcpServerv4OptionValue { }\n"
    ) + script
    path = tmp_path / "cmdlets.ps1"
    path.write_bytes(harness.encode("utf-8"))
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True
