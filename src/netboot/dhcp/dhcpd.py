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

    SETTINGS = frozenset({"keyname", "keyfile", "conditions"})

    def __init__(self, uri: str):
        super().__init__(uri)
        parts = urlsplit(uri)
        self.hostname = parts.hostname or "localhost"
        self.port = parts.port or 7911
        self.keyname = self.settings.get("keyname")
        self.conditions = str(self.settings.get("conditions", "if")).lower()
        if self.conditions not in ("if", "group"):
            from .. import PixieConfigError

            raise PixieConfigError(
                f"dhcpd: conditions must be 'if' or 'group', not "
                f"{self.conditions!r}"
            )
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
        conditions = list(self.conditions_for(netboot).values())
        extras = self.extras(netboot, "add")
        group = None
        if conditions and self.conditions == "group":
            # A host belongs to exactly one group, so one group carries every
            # condition that applies, named after them in declaration order.
            group = "-".join(c.name for c in conditions)
        elif conditions:
            # Inline: the conditional statements live on the host itself. Always
            # correct, needs no second OMAPI object, and the default for that
            # reason.
            extras = extras + [render_condition(c) for c in conditions]
        statements = render_statements(options, extras)
        connection = self.connect()
        try:
            if group is not None:
                self._ensure_group(connection, group, conditions)
            connection.add_host_supersede(
                str(target.ip),
                mac,
                str(target._id),
                statements=statements or None,
            )
            if group is not None:
                # Set after the host exists: `change_group` takes the host name.
                connection.change_group(str(target._id), group)
        finally:
            _close(connection)

    def keep_target(self, netboot: "_ty.Any", completion) -> None:
        """Supersede the host with the completion statements instead of deleting it.

        `add_host_supersede` writes the host whole, so this is one call and there
        is no window where the host exists with the installer's filename and the
        new statements half applied.
        """
        target = netboot.target
        options = completion.apply_to(self.options_for(netboot))
        statements = render_statements(options, self.extras(netboot, "remove"))
        connection = self.connect()
        try:
            connection.add_host_supersede(
                str(target.ip),
                _mac(target),
                str(target._id),
                statements=statements or None,
            )
        finally:
            _close(connection)

    def _ensure_group(self, connection, group: str, conditions) -> None:
        """Create the group carrying these conditions, unless it is already there.

        OMAPI has no group lookup, so "already there" is what `add_group`
        raising tells us -- and that is the step-1 case, not a failure. A group
        created this way shares the caveat every OMAPI object has: it does not
        survive a dhcpd restart, which is why `condition_recipe` prints the
        `dhcpd.conf` block that does.
        """
        statements = " ".join(render_condition(c) for c in conditions)
        try:
            connection.add_group(group, statements)
        except Exception as exc:  # noqa: BLE001 - the library raises its own types
            if "exist" not in str(exc).lower() and type(exc).__name__ not in (
                "OmapiError",
                "OmapiErrorNotFound",
            ):
                raise
            LOGGER.debug("dhcpd group %s already present (%s)", group, exc)

    def ensure_condition(self, netboot: "_ty.Any", condition) -> str:
        """dhcpd expresses a condition in the statements it already writes.

        Nothing to create in `if` mode -- the statements go on the host, so the
        name is only a label. In `group` mode the group is created with the
        target, because `change_group` needs the host to exist first.
        """
        return condition.name

    def condition_recipe(self, netboot: "_ty.Any", condition) -> str:
        """The `dhcpd.conf` group an admin can add, as the durable alternative.

        An OMAPI group vanishes on restart; this one does not. Printing it is the
        recommended path for dhcpd rather than a fallback.
        """
        return (
            f"# netboot: add to dhcpd.conf so {condition.name!r} survives a "
            f"restart\n"
            f'group "{condition.name}" {{\n'
            f"  {render_condition(condition)}\n"
            f"}}"
        )

    def remove_condition_member(self, netboot: "_ty.Any", condition) -> None:
        """Drop the host out of its group, leaving the group for other hosts.

        Superseding the host without a group is the way: OMAPI has no "unset the
        group" operation, and `add_host_supersede` writes the host whole.
        """
        if self.conditions != "group":
            return
        target = netboot.target
        connection = self.connect()
        try:
            connection.add_host_supersede(
                str(target.ip),
                _mac(target),
                str(target._id),
                statements=None,
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


#: `match` key -> the dhcpd expression that tests it. `exists` first, because
#: testing an absent option is an error in dhcpd, not false.
_CONDITION_TESTS = {
    "user-class": "exists user-class and option user-class = {value}",
    "vendor-class": (
        "exists vendor-class-identifier and " "option vendor-class-identifier = {value}"
    ),
}


def render_condition(condition) -> str:
    """One condition as a dhcpd `if` statement, values escaped.

    The body is the same renderer the host's own options go through, so a
    conditional option cannot be escaped differently from an unconditional one.
    """
    from .. import PixieConfigError

    tests = []
    for key, value in condition.match.items():
        text = str(value)
        if any(ch in text for ch in '\r\n\x00"'):
            # A quote would close the literal and the rest would be dhcpd source.
            raise PixieConfigError(
                f"dhcp_when.{condition.name} cannot match {key} on {text!r}: a "
                "newline, NUL or quote cannot appear in a dhcpd test"
            )
        tests.append(_CONDITION_TESTS[key].format(value=_quote_text(text)))
    body = render_statements(condition.options)
    return "if " + " and ".join(tests) + " { " + body + " }"


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


def _quote_text(text: str) -> str:
    """A dhcpd string literal. The one place `"` and `\\` are escaped."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
        return _quote_text(text)
    if not _BARE.match(text):
        raise PixieConfigError(
            f"dhcpd option {keyword!r} expects an address or number, got "
            f"{text!r}; quote it as text by using a text option, or pass it "
            "through raw.dhcpd if you know what dhcpd expects"
        )
    return text
