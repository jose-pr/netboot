"""Windows DHCP Server backend: its cmdlets or netsh, over ssh or WinRM.

Windows DHCP has no line protocol to speak, so netboot drives the server's own
tooling where the operator already has access. Two independent choices:

- **`transport=`** -- how we get a shell: `ssh` (the system client) or `winrm`.
- **`method=`** -- what we run there: `powershell` (the `DhcpServer` module's
  cmdlets, the default) or `netsh` (`netsh dhcp server ...`), for a host where
  the DHCP Server PowerShell module is not installed -- a Server Core box
  without the RSAT feature, or an older release.

The transport and the DHCP server stay separate either way: the shell runs on the
URI's host, and the commands act on `server=` (`-ComputerName`, or netsh's
`\\server`) when that differs.

**No value is ever interpolated into the script.** The parameters travel as a
JSON payload that PowerShell parses, with one escaping rule applied once (a
single-quoted PowerShell string escapes `'` by doubling it). Values arrive as
data, not as source — which is the lesson `shell_quote` and the dhcpd
`statements` fragment each taught this code base the hard way.

That holds for `method=netsh` too, and it is why netsh is invoked *from*
PowerShell rather than from a command line: each netsh argument stays an element
of a JSON array and is passed through `&  netsh @args`, so no quoting rule has to
hold for a value. PowerShell is present on every Windows that has netsh, so this
costs nothing and removes the whole command-line quoting question.
"""

from __future__ import annotations

import json as _json
import os as _os
import subprocess as _subprocess
import typing as _ty
from urllib.parse import urlsplit

from ..logging import LOGGER
from . import DhcpServer

#: Generic option name -> Windows DHCP option id.
_OPTION_IDS = {
    "router": 3,
    "subnet-mask": 1,
    "broadcast-address": 28,
    "domain-name-servers": 6,
    "domain-name": 15,
    "ntp-servers": 42,
    "lease-time": 51,
    "tftp-server-name": 66,
    "boot-file-name": 67,
    "vendor-class-identifier": 60,
}

#: Windows DHCP option id -> the data type `netsh ... set reservedoptionvalue`
#: wants. The cmdlets infer this from the option definition; netsh does not, and
#: gets it wrong silently if told `STRING` for an address. Anything not listed
#: (including a raw `option-NN`) is passed as STRING, which is what an unknown
#: vendor option almost always is.
_OPTION_TYPES = {
    1: "IPADDRESS",  # subnet mask
    3: "IPADDRESS",  # router
    6: "IPADDRESS",  # domain name servers
    15: "STRING",  # domain name
    28: "IPADDRESS",  # broadcast address
    42: "IPADDRESS",  # ntp servers
    51: "DWORD",  # lease time
    60: "STRING",  # vendor class identifier
    66: "STRING",  # tftp server name
    67: "STRING",  # boot file name
}

#: Where a WinRM password is read from. Never the URI.
PASSWORD_ENV_VAR = "PIXIE_WINDHCP_PASSWORD"

#: Runs each `Commands` entry as `netsh <args...>`. The arguments stay array
#: elements -- never a string PowerShell re-parses -- so a value cannot become
#: syntax. netsh's own text goes to the log; its **exit code** is what decides,
#: because the success line ("Command completed successfully.") is localised and
#: matching it would break on a non-English host. `Ignore` is for a delete that
#: may have nothing to delete, so cleanup can re-run.
#: netsh exits 0 and prints its localised success line even when it rejected
#: what it was told: setting option 6 to two name servers, one of which does not
#: answer, drops that one, says "not a valid DNS Server", says "Command completed
#: successfully", and exits 0 (measured on Windows Server 2025, 2026-10-02). So
#: the end state is read back instead of trusted. `dump` is the one netsh output
#: that is machine-readable and not prose -- it emits the commands that would
#: recreate the state, quoted one token per value, in any locale.
_NETSH_VERIFY = [
    "if ($p.Verify) {",
    "  $d = @($p.VerifyArgs)",
    '  $dump = (& netsh @d 2>&1 | Out-String) -split "`r?`n"',
    "  $bad = @()",
    "  foreach ($v in $p.Verify) {",
    "    $re = 'set reservedoptionvalue\\s+' + [regex]::Escape($p.IPAddress) +",
    "          '\\s+' + $v.Id + '\\s'",
    "    $line = $dump | Where-Object { $_ -match $re } | Select-Object -First 1",
    '    if (-not $line) { $bad += "option $($v.Id) was not applied"; continue }',
    "    $tail = $line -replace ('^.*' + $re), ''",
    "    $tail = $tail -replace '^\\S+\\s*', ''",
    '    $n = @([regex]::Matches($tail, \'"[^"]*"|\\S+\')).Count',
    "    if ($n -lt $v.Count) {",
    '      $bad += "option $($v.Id) kept $n of $($v.Count) values"',
    "    }",
    "  }",
    "  if ($bad) {",
    '    throw "netsh reported success but the server disagrees: " +',
    "          ($bad -join '; ')",
    "  }",
    "}",
]

_NETSH_BODY = [
    "foreach ($c in $p.Commands) {",
    # `@a` on a *variable* is splatting, one argument per element. `@($c.Args)`
    # is an array subexpression -- it passes the whole array as one argument,
    # which netsh then sees as a single space-filled token. Measured by running
    # the generated script with a shim in place of netsh.
    "  $a = @($c.Args)",
    "  $out = & netsh @a 2>&1 | Out-String",
    "  Write-Verbose $out",
    "  if ($LASTEXITCODE -ne 0 -and -not $c.Ignore) {",
    "    throw \"netsh $($c.Args -join ' ') failed ($LASTEXITCODE): $out\"",
    "  }",
    "}",
]

_SCRIPT = """$ErrorActionPreference = 'Stop'
$p = ConvertFrom-Json '{payload}'
{body}
ConvertTo-Json @{{ ok = $true }}
"""


class windhcp(DhcpServer):  # noqa: N801 - the class name is the URI scheme
    """`windhcp://[user@]host/?transport=ssh|winrm&method=powershell|netsh&server=<dhcp server>`

    Every other query key is a client option. The scope comes from the zone
    when the target is applied, not from here.
    """

    SETTINGS = frozenset({"transport", "method", "server", "auth", "port", "ssl"})

    def __init__(self, uri: str):
        super().__init__(uri)
        from .. import PixieConfigError

        parts = urlsplit(uri)
        self.hostname = parts.hostname or "localhost"
        self.user = parts.username or ""
        self.transport = self.settings.get("transport", "ssh")
        if self.transport not in ("ssh", "winrm"):
            raise PixieConfigError(
                f"windhcp: transport must be ssh or winrm, not {self.transport!r}"
            )
        self.method = str(self.settings.get("method", "powershell")).lower()
        if self.method not in ("powershell", "netsh"):
            raise PixieConfigError(
                f"windhcp: method must be powershell or netsh, not {self.method!r}"
            )
        self.server = self.settings.get("server") or ""
        self.auth = self.settings.get("auth", "ntlm")
        self.port = int(
            self.settings.get("port") or (22 if self.transport == "ssh" else 5985)
        )
        self.ssl = str(self.settings.get("ssl", "")).lower() in ("1", "true", "yes")

    # -- the DhcpServer contract ------------------------------------------

    def add_target(self, netboot: "_ty.Any"):
        if self.method == "netsh":
            return self._add_target_netsh(netboot)
        target = netboot.target
        options = self.options_for(netboot)
        payload = {
            "ScopeId": str(_scope(netboot)),
            "IPAddress": str(target.ip),
            "ClientId": _client_id(target),
            "Name": str(target._id),
            "ComputerName": self.server,
            # A list, never a comma-joined string: `Set-DhcpServerv4OptionValue
            # -Value` takes String[], and refuses "a,b" with "Parameters for
            # option value to be set for option ID 6 do not match with option
            # definition" -- which, under $ErrorActionPreference='Stop', also
            # abandoned every option after it. Measured against a real Windows
            # Server 2025 DHCP server, 2026-10-02.
            "Options": [
                {"Id": option_id, "Value": [str(v) for v in _values(value)]}
                for option_id, value in _option_ids(options)
            ],
        }
        body = [
            "$common = @{}",
            "if ($p.ComputerName) { $common['ComputerName'] = $p.ComputerName }",
            "Add-DhcpServerv4Reservation -ScopeId $p.ScopeId -IPAddress $p.IPAddress"
            " -ClientId $p.ClientId -Name $p.Name @common",
            "foreach ($o in $p.Options) {",
            "  Set-DhcpServerv4OptionValue -ReservedIP $p.IPAddress -OptionId $o.Id"
            " -Value $o.Value @common",
            "}",
        ]
        body.extend(_script_lines(self.extras(netboot, "add")))
        self.run(payload, body)

    def remove_target(self, netboot: "_ty.Any"):
        if self.method == "netsh":
            return self._remove_target_netsh(netboot)
        target = netboot.target
        payload = {
            "ScopeId": str(_scope(netboot)),
            "ClientId": _client_id(target),
            "ComputerName": self.server,
        }
        body = [
            "$common = @{}",
            "if ($p.ComputerName) { $common['ComputerName'] = $p.ComputerName }",
            # Removing a reservation that is not there is success: cleanup re-runs.
            "$existing = Get-DhcpServerv4Reservation -ScopeId $p.ScopeId @common"
            " -ErrorAction SilentlyContinue |"
            " Where-Object { $_.ClientId -eq $p.ClientId }",
            "if ($existing) {",
            "  Remove-DhcpServerv4Reservation -ScopeId $p.ScopeId"
            " -ClientId $p.ClientId @common",
            "}",
        ]
        body.extend(_script_lines(self.extras(netboot, "remove")))
        self.run(payload, body)

    # -- method=netsh ------------------------------------------------------

    def _netsh_scope(self, scope: str) -> "list[str]":
        """The `dhcp server [\\host] scope <scope>` prefix every command shares."""
        prefix = ["dhcp", "server"]
        if self.server:
            # netsh addresses a remote server as a UNC-style name; it accepts an
            # address too, which is why this is not validated as a hostname.
            prefix.append(
                self.server if self.server.startswith("\\\\") else f"\\\\{self.server}"
            )
        prefix += ["scope", scope]
        return prefix

    def _add_target_netsh(self, netboot: "_ty.Any"):
        target = netboot.target
        options = self.options_for(netboot)
        scope = str(_scope(netboot))
        base = self._netsh_scope(scope)
        commands = [
            {
                # `add reservedip <ip> <mac> [name] [comment] [BOTH|DHCP|BOOTP]`.
                # BOTH matches the cmdlets' default, so switching method does not
                # change which protocols the reservation answers.
                "Args": base
                + [
                    "add",
                    "reservedip",
                    str(target.ip),
                    _netsh_client_id(target),
                    str(target._id),
                    "netboot",
                    "BOTH",
                ],
                "Ignore": False,
            }
        ]
        for option_id, value in _option_ids(options):
            commands.append(
                {
                    "Args": base
                    + [
                        "set",
                        "reservedoptionvalue",
                        str(target.ip),
                        str(option_id),
                        _OPTION_TYPES.get(option_id, "STRING"),
                    ]
                    # Each value is its own argument: that is how netsh takes a
                    # multi-valued option (two name servers, say).
                    + [str(item) for item in _values(value)],
                    "Ignore": False,
                }
            )
        verify = [
            {"Id": str(option_id), "Count": len(_values(value))}
            for option_id, value in _option_ids(options)
        ]
        extra_commands, extra_lines = _split_extras(self.extras(netboot, "add"))
        # A netsh command from `extras()` runs after the reservation and its
        # options, which is the order a conditional needs: the thing it
        # overrides has to exist first.
        commands.extend(extra_commands)
        body = list(_NETSH_BODY)
        body.extend(extra_lines)
        # Verification runs last, after any extra command, so a conditional that
        # replaces an option is not reported as the option going missing.
        body.extend(_NETSH_VERIFY)
        payload = {
            "Commands": commands,
            "IPAddress": str(target.ip),
            "Verify": verify,
            "VerifyArgs": base + ["dump"],
        }
        return self.run(payload, body)

    def _remove_target_netsh(self, netboot: "_ty.Any"):
        target = netboot.target
        base = self._netsh_scope(str(_scope(netboot)))
        commands = [
            {
                # Deleting a reservation that is not there is success here, the
                # same as in the PowerShell path: cleanup re-runs.
                "Args": base
                + [
                    "delete",
                    "reservedip",
                    str(target.ip),
                    _netsh_client_id(target),
                ],
                "Ignore": True,
            }
        ]
        extra_commands, extra_lines = _split_extras(self.extras(netboot, "remove"))
        commands.extend(extra_commands)
        body = list(_NETSH_BODY)
        body.extend(extra_lines)
        return self.run({"Commands": commands}, body)

    # -- running PowerShell ------------------------------------------------

    def script_for(self, payload: dict, body: "list[str]") -> str:
        """The script we will run: parameters as data, never as source."""
        encoded = _json.dumps(payload).replace("'", "''")
        return _SCRIPT.format(payload=encoded, body="\n".join(body))

    def run(self, payload: dict, body: "list[str]") -> str:
        script = self.script_for(payload, body)
        if self.transport == "winrm":
            return self._run_winrm(script)
        return self._run_ssh(script)

    def _run_ssh(self, script: str) -> str:
        """Feed the script to PowerShell over the system ssh client."""
        from .. import PixieConfigError

        destination = f"{self.user}@{self.hostname}" if self.user else self.hostname
        argv = [
            "ssh",
            "-p",
            str(self.port),
            destination,
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "-",
        ]
        LOGGER.debug("windhcp ssh: %s", argv)
        completed = _subprocess.run(
            argv, input=script, capture_output=True, text=True, timeout=120
        )
        if completed.returncode != 0:
            raise PixieConfigError(
                f"windhcp: PowerShell over ssh failed ({completed.returncode}): "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
        return completed.stdout

    def _run_winrm(self, script: str) -> str:
        from .. import PixieConfigError

        try:
            import winrm
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise ImportError(
                "the Windows DHCP backend over WinRM requires netboot's 'winrm' "
                "extra: pip install netboot[winrm]"
            ) from exc

        scheme = "https" if self.ssl else "http"
        endpoint = f"{scheme}://{self.hostname}:{self.port}/wsman"
        password = _os.environ.get(PASSWORD_ENV_VAR, "")
        session = winrm.Session(
            endpoint, auth=(self.user, password), transport=self.auth
        )
        result = session.run_ps(script)
        if result.status_code != 0:
            raise PixieConfigError(
                f"windhcp: PowerShell over WinRM failed ({result.status_code}): "
                f"{_text(result.std_err) or _text(result.std_out)}"
            )
        return _text(result.std_out)


def _split_extras(extras: "list") -> "tuple[list[dict], list[str]]":
    """Sort `extras()` output into netsh commands and PowerShell lines.

    A mapping is a netsh command (`{"Args": [...], "Ignore": False}`); `Args` may
    be a list or a single string that is split on whitespace, because a config
    `raw.windhcp.add=dhcp server scope ... add ...` has nowhere to put a list.
    Anything else is a PowerShell line, which is valid under either method --
    netsh mode is still PowerShell, it just calls netsh.
    """
    commands: "list[dict]" = []
    lines: "list[str]" = []
    for extra in extras:
        if isinstance(extra, dict):
            args = extra.get("Args", [])
            if isinstance(args, str):
                args = args.split()
            commands.append(
                {"Args": [str(a) for a in args], "Ignore": bool(extra.get("Ignore"))}
            )
        else:
            lines.append(str(extra))
    return commands, lines


def _script_lines(extras: "list") -> "list[str]":
    """`extras()` output as PowerShell lines, for the cmdlet method.

    A netsh command reaching the cmdlet path is turned into the `netsh` call it
    describes rather than dropped: an override that knows netsh is still usable
    when the method is `powershell`, and silently ignoring it would be worse.
    """
    lines: "list[str]" = []
    commands, plain = _split_extras(extras)
    for command in commands:
        rendered = " ".join(_ps_quote(arg) for arg in command["Args"])
        if command["Ignore"]:
            lines.append(f"& netsh {rendered} 2>&1 | Out-Null")
        else:
            lines.append(f"$out = & netsh {rendered} 2>&1 | Out-String")
            lines.append(
                'if ($LASTEXITCODE -ne 0) { throw "netsh failed '
                f'($LASTEXITCODE): $out" }}'
            )
    lines.extend(plain)
    return lines


def _ps_quote(value: str) -> str:
    """A PowerShell single-quoted literal. Only for arguments netboot builds."""
    return "'" + str(value).replace("'", "''") + "'"


def _text(value) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _scope(ctx) -> str:
    """The Windows scope for this target: the zone's, resolved at apply time."""
    from .. import PixieConfigError

    zone = ctx.dhcpzone
    declared = getattr(zone, "scope", None)
    if declared:
        return str(declared)
    network = getattr(zone, "network", None)
    if network is None:
        raise PixieConfigError(
            f"windhcp: zone {getattr(zone, '_id', '?')!r} has neither a network "
            "nor a `scope`, and a Windows scope is identified by its network "
            "address"
        )
    return str(network.network_address)


def _require_mac(target):
    """The target's MAC, or a clear error: a reservation is identified by one."""
    from .. import PixieLookupError
    from ..engine import PixieTarget

    mac = target.mac
    if not str(mac) or str(mac) == PixieTarget._NULL_MAC:
        raise PixieLookupError(
            f"target {target._id!r} has no MAC address, and a Windows DHCP "
            "reservation is identified by one"
        )
    return mac


def _client_id(target) -> str:
    """Windows identifies a reservation by MAC, in its hyphen spelling."""
    return _require_mac(target).as_str("-")


def _netsh_client_id(target) -> str:
    """netsh wants the MAC as bare hex; the cmdlets want it hyphenated."""
    return _require_mac(target).hex()


def _values(value) -> "list":
    """A value as the list netsh takes, one argument per entry."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _option_ids(options) -> "list[tuple[int, object]]":
    """Generic options as (option id, value) pairs Windows understands.

    The value is **unformatted**: the cmdlets take a multi-valued option as one
    comma-joined string (`_value`), netsh as one argument per value (`_values`),
    and joining it here would have made the second impossible.
    """
    pairs: "list[tuple[int, object]]" = []
    for name, value in options.items():
        option_id = _OPTION_IDS.get(name)
        if option_id is None:
            if name.startswith("option-"):
                option_id = int(name[len("option-") :])
            elif name.isdigit():
                option_id = int(name)
            else:  # pragma: no cover - unknown names are refused earlier
                continue
        pairs.append((option_id, value))
    return pairs


def _value(value) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)
