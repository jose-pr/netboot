"""ISC dhcpd backend, over OMAPI.

dhcpd has no way to take a reservation without rewriting and reloading its
config — except OMAPI, which is what this uses. Two things about that are worth
knowing before you rely on it:

* the host object's ``statements`` are a fragment of dhcpd's **config
  language**, so every value interpolated into it is escaped or refused here;
* a host added over OMAPI is **not** written to ``dhcpd.conf``, and does not
  survive a restart unless the server keeps it in a lease/host database. An
  operator who restarts dhcpd and finds every reservation gone should know that
  is dhcpd's design, not netboot losing them.
"""

from __future__ import annotations

import os as _os
import re as _re
import typing as _ty
from urllib.parse import urlsplit

from ..logging import LOGGER
from . import DhcpServer

#: Generic name -> what dhcpd calls it. `filename`/`next-server` are statements
#: in their own right; the rest are `option <name> <value>;`.
_STATEMENTS = {"boot-file-name": "filename", "next-server": "next-server"}
_OPTIONS = {
    "router": "routers",  # note the plural: dhcpd's own spelling
    "domain-name-servers": "domain-name-servers",
    "domain-name": "domain-name",
    "domain-search": "domain-search",
    "ntp-servers": "ntp-servers",
    "subnet-mask": "subnet-mask",
    "broadcast-address": "broadcast-address",
    "lease-time": "dhcp-lease-time",
    "tftp-server-name": "tftp-server-name",
    "host-name": "host-name",
    "vendor-class-identifier": "vendor-class-identifier",
}
#: Values dhcpd expects as quoted text rather than as an address or a number.
_TEXT = {
    "filename",
    "domain-name",
    "domain-search",
    "tftp-server-name",
    "host-name",
    "vendor-class-identifier",
}
#: What an unquoted value may contain: addresses, numbers, lists of them.
_BARE = _re.compile(r"^[0-9a-fA-F.:, ]+$")

#: Where the OMAPI secret is read from when no keyfile is configured.
KEY_ENV_VAR = "PIXIE_DHCPD_OMAPI_KEY"


class dhcpd(DhcpServer):  # noqa: N801 - the class name is the URI scheme
    """`dhcpd://host:7911/?keyname=omapi_key&keyfile=/etc/dhcp/omapi.key`

    The key is the HMAC-MD5 secret matching `omapi-key`/`omapi-port` in
    `dhcpd.conf`. It is read from `keyfile=`, or from `$PIXIE_DHCPD_OMAPI_KEY`
    — never from the URI, which ends up in logs and config repositories.
    """

    SETTINGS = frozenset({"keyname", "keyfile"})

    def __init__(self, uri: str):
        super().__init__(uri)
        parts = urlsplit(uri)
        self.hostname = parts.hostname or "localhost"
        self.port = parts.port or 7911
        self.keyname = self.settings.get("keyname")
        self.keyfile = self.settings.get("keyfile")

    # -- transport --------------------------------------------------------

    def _secret(self) -> "str|None":
        if self.keyfile:
            with open(self.keyfile, encoding="utf-8") as handle:
                return handle.read().strip()
        return _os.environ.get(KEY_ENV_VAR)

    def connect(self):
        """An authenticated OMAPI connection to this dhcpd."""
        try:
            import pypureomapi
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise ImportError(
                "the ISC dhcpd backend requires netboot's 'dhcpd' extra: "
                "pip install netboot[dhcpd]"
            ) from exc

        secret = self._secret()
        if self.keyname and not secret:
            from .. import PixieConfigError

            raise PixieConfigError(
                f"dhcpd: keyname={self.keyname!r} is set but no secret was found; "
                f"give keyfile=<path> or set {KEY_ENV_VAR}"
            )
        username = self.keyname.encode() if self.keyname else None
        key = secret.encode() if secret else None
        LOGGER.debug("omapi connect %s:%s", self.hostname, self.port)
        return pypureomapi.Omapi(self.hostname, self.port, username, key, timeout=30)

    # -- the DhcpServer contract ------------------------------------------

    def add_target(self, netboot: "_ty.Any"):
        target = netboot.target
        mac = _mac(target)
        options = self.options_for(netboot)
        statements = render_statements(options, self.options.raw_for("dhcpd"))
        connection = self.connect()
        try:
            connection.add_host_supersede(
                str(target.ip),
                mac,
                str(target._id),
                statements=statements or None,
            )
        finally:
            _close(connection)

    def remove_target(self, netboot: "_ty.Any"):
        mac = _mac(netboot.target)
        connection = self.connect()
        try:
            connection.del_host(mac)
        except Exception as exc:  # noqa: BLE001 - the library's own not-found type
            if type(exc).__name__ != "OmapiErrorNotFound":
                raise
            LOGGER.debug("omapi: no host for %s; nothing to remove", mac)
        finally:
            _close(connection)


def _close(connection) -> None:
    try:
        connection.close()
    except Exception:  # pragma: no cover - closing must not mask the real error
        LOGGER.debug("omapi close failed", exc_info=True)


def _mac(target) -> str:
    from .. import PixieLookupError
    from ..engine import PixieTarget

    mac = str(target.mac)
    if not mac or mac == PixieTarget._NULL_MAC:
        raise PixieLookupError(
            f"target {target._id!r} has no MAC address, and a dhcpd host "
            "reservation is identified by one"
        )
    return mac


def render_statements(options, raw: "list[str]|None" = None) -> str:
    """Render options as a dhcpd config fragment, escaping every value.

    `statements` is config *source*, so an unescaped value could end one
    statement and start another. Text values are quoted with `"` and `\\`
    escaped; anything meant to be an address or a number must look like one.
    """
    parts: "list[str]" = []
    for name, value in options.items():
        keyword = _STATEMENTS.get(name)
        if keyword:
            parts.append(f"{keyword} {_render(keyword, value)};")
            continue
        option = _OPTIONS.get(name)
        if option is None:
            if name.startswith("option-"):
                option = f"dhcp-option-{name[len('option-'):]}"
            elif name.isdigit():
                option = f"dhcp-option-{name}"
            else:  # pragma: no cover - unknown names are refused earlier
                continue
        parts.append(f"option {option} {_render(option, value)};")
    parts.extend(raw or [])
    return " ".join(parts)


def _render(keyword: str, value) -> str:
    """One value, quoted or bare, never able to end its statement."""
    from .. import PixieConfigError

    if isinstance(value, (list, tuple)):
        return ", ".join(_render(keyword, item) for item in value)
    text = str(value)
    if any(ch in text for ch in "\r\n\x00"):
        raise PixieConfigError(
            f"dhcpd option {keyword!r} value contains a newline or NUL, which "
            "cannot be written into a dhcpd statement: " + repr(text)
        )
    if keyword in _TEXT:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if not _BARE.match(text):
        raise PixieConfigError(
            f"dhcpd option {keyword!r} expects an address or number, got "
            f"{text!r}; quote it as text by using a text option, or pass it "
            "through raw.dhcpd if you know what dhcpd expects"
        )
    return text
