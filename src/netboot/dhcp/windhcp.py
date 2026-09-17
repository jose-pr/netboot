"""Windows DHCP Server backend: its own cmdlets, over ssh or WinRM.

Windows DHCP has no line protocol to speak, so netboot runs the `DhcpServer`
PowerShell module where the operator already has access. The transport and the
DHCP server are therefore separate: PowerShell runs on the URI's host, and the
cmdlets act on `server=` (`-ComputerName`) when that differs.

**No value is ever interpolated into the script.** The parameters travel as a
JSON payload that PowerShell parses, with one escaping rule applied once (a
single-quoted PowerShell string escapes `'` by doubling it). Values arrive as
data, not as source — which is the lesson `shell_quote` and the dhcpd
`statements` fragment each taught this code base the hard way.
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

#: Where a WinRM password is read from. Never the URI.
PASSWORD_ENV_VAR = "PIXIE_WINDHCP_PASSWORD"

_SCRIPT = """$ErrorActionPreference = 'Stop'
$p = ConvertFrom-Json '{payload}'
{body}
ConvertTo-Json @{{ ok = $true }}
"""


class windhcp(DhcpServer):  # noqa: N801 - the class name is the URI scheme
    """`windhcp://[user@]host/?transport=ssh|winrm&server=<dhcp server>`

    Every other query key is a client option. The scope comes from the zone
    when the target is applied, not from here.
    """

    SETTINGS = frozenset({"transport", "server", "auth", "port", "ssl"})

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
        self.server = self.settings.get("server") or ""
        self.auth = self.settings.get("auth", "ntlm")
        self.port = int(
            self.settings.get("port") or (22 if self.transport == "ssh" else 5985)
        )
        self.ssl = str(self.settings.get("ssl", "")).lower() in ("1", "true", "yes")

    # -- the DhcpServer contract ------------------------------------------

    def add_target(self, netboot: "_ty.Any"):
        target = netboot.target
        options = self.options_for(netboot)
        payload = {
            "ScopeId": str(_scope(netboot)),
            "IPAddress": str(target.ip),
            "ClientId": _client_id(target),
            "Name": str(target._id),
            "ComputerName": self.server,
            "Options": [
                {"Id": option_id, "Value": value}
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
        body.extend(self.options.raw_for("windhcp"))
        self.run(payload, body)

    def remove_target(self, netboot: "_ty.Any"):
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
        self.run(payload, body)

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


def _client_id(target) -> str:
    """Windows identifies a reservation by MAC, in its hyphen spelling."""
    from .. import PixieLookupError
    from ..engine import PixieTarget

    mac = target.mac
    if not str(mac) or str(mac) == PixieTarget._NULL_MAC:
        raise PixieLookupError(
            f"target {target._id!r} has no MAC address, and a Windows DHCP "
            "reservation is identified by one"
        )
    return mac.as_str("-")


def _option_ids(options) -> "list[tuple[int, str]]":
    """Generic options as (option id, value) pairs Windows understands."""
    pairs: "list[tuple[int, str]]" = []
    for name, value in options.items():
        option_id = _OPTION_IDS.get(name)
        if option_id is None:
            if name.startswith("option-"):
                option_id = int(name[len("option-") :])
            elif name.isdigit():
                option_id = int(name)
            else:  # pragma: no cover - unknown names are refused earlier
                continue
        pairs.append((option_id, _value(value)))
    return pairs


def _value(value) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)
