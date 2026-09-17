"""dnsmasq backend: write reservation files, reload only if we must.

dnsmasq re-reads the files given to ``--dhcp-hostsfile`` / ``--dhcp-optsfile``
on SIGHUP — but **not** its main config file, so writing into ``/etc/dnsmasq.d``
would need a restart. Better still, ``--dhcp-hostsdir`` / ``--dhcp-optsdir`` take
a directory and pick up changed or new files **with no signal at all**, which is
why directory mode is the recommended shape here: netboot writes a file and is
done, with no shell access and no right to signal anything.

Paths go through :func:`netboot.utils.misc.parse_path`, so the same code serves
a dnsmasq on this host and one three racks away over ``sftp://``.
"""

from __future__ import annotations

import os as _os
import shlex as _shlex
import subprocess as _subprocess
import typing as _ty

from ..logging import LOGGER
from ..utils.misc import parse_path
from . import DhcpServer

#: Generic option name -> dnsmasq option number.
_CODES = {
    "router": 3,
    "subnet-mask": 1,
    "broadcast-address": 28,
    "domain-name-servers": 6,
    "domain-name": 15,
    "domain-search": 119,
    "ntp-servers": 42,
    "lease-time": 51,
    "tftp-server-name": 66,
    "boot-file-name": 67,
    "vendor-class-identifier": 60,
}

_MARK_START = "# >>> netboot {tag}"
_MARK_END = "# <<< netboot {tag}"


class dnsmasq(DhcpServer):  # noqa: N801 - the class name is the URI scheme
    """`dnsmasq://[host]/?hostsfile=...&optsfile=...&reload=...`

    `hostsfile`/`optsfile` may be a directory (one file per target, preferred)
    or a single file (netboot owns a marked region inside it). Each may carry
    its own scheme; a bare path is local when the URI has no host, and
    `sftp://<host>/<path>` when it does.
    """

    SETTINGS = frozenset({"hostsfile", "optsfile", "reload", "pidfile"})

    def __init__(self, uri: str):
        super().__init__(uri)
        from .. import PixieConfigError

        from urllib.parse import urlsplit

        parts = urlsplit(uri)
        self.host = parts.hostname or ""
        self.user = parts.username or ""

        hostsfile = self.settings.get("hostsfile") or parts.path
        if not hostsfile or hostsfile == "/":
            raise PixieConfigError(
                "dnsmasq needs hostsfile=<path> (the dnsmasq --dhcp-hostsfile or "
                "--dhcp-hostsdir location)"
            )
        self.hostsfile = self._path(hostsfile)
        optsfile = self.settings.get("optsfile")
        self.optsfile = self._path(optsfile) if optsfile else None
        self.reload_command = self.settings.get("reload")
        self.pidfile = self.settings.get("pidfile", "/run/dnsmasq/dnsmasq.pid")
        self._reload_warned = False

    def _path(self, value: str):
        """A configured path: its own scheme, else local or sftp on our host."""
        if "://" in value:
            _require_sftp(value)
            return parse_path(value)
        if self.host:
            user = f"{self.user}@" if self.user else ""
            remote = f"sftp://{user}{self.host}{value}"
            _require_sftp(remote)
            return parse_path(remote)
        return parse_path(value)

    # -- the DhcpServer contract ------------------------------------------

    def add_target(self, netboot: "_ty.Any"):
        options = self.options_for(netboot)
        target = netboot.target
        tag = _tag(target)
        self._write(self.hostsfile, tag, [_host_line(target, tag)])
        if self.optsfile is not None:
            self._write(self.optsfile, tag, _option_lines(tag, options))
        self._reload()

    def remove_target(self, netboot: "_ty.Any"):
        tag = _tag(netboot.target)
        self._write(self.hostsfile, tag, [])
        if self.optsfile is not None:
            self._write(self.optsfile, tag, [])
        self._reload()

    # -- writing ----------------------------------------------------------

    def _write(self, base, tag: str, lines: "list[str]") -> None:
        """Put `lines` under `tag`, or remove that tag's entry when empty."""
        if _is_directory(base):
            self._write_file(base / f"{tag}.conf", lines)
        else:
            self._write_region(base, tag, lines)

    def _write_file(self, path, lines: "list[str]") -> None:
        if not lines:
            try:
                path.unlink()
            except FileNotFoundError:
                pass  # disarming twice is quiet: pxe_complete may re-run
            return
        _atomic_write(path, "\n".join(lines) + "\n")

    def _write_region(self, path, tag: str, lines: "list[str]") -> None:
        """Replace only netboot's own marked region, leaving every other line."""
        start, end = _MARK_START.format(tag=tag), _MARK_END.format(tag=tag)
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            existing = []

        kept, skipping = [], False
        for line in existing:
            if line.strip() == start:
                skipping = True
                continue
            if line.strip() == end:
                skipping = False
                continue
            if not skipping:
                kept.append(line)

        if lines:
            kept += [start, *lines, end]
        _atomic_write(path, "\n".join(kept) + ("\n" if kept else ""))

    # -- reloading --------------------------------------------------------

    def _reload(self) -> None:
        from .. import PixieConfigError

        if not self.reload_command:
            if _is_directory(self.hostsfile):
                if not self._reload_warned:
                    LOGGER.info(
                        "dnsmasq: no reload configured; assuming --dhcp-hostsdir/"
                        "--dhcp-optsdir, which re-read changed files by themselves"
                    )
                    self._reload_warned = True
                return
            raise PixieConfigError(
                "dnsmasq: reload=<command> is required when hostsfile is a file "
                "(dnsmasq re-reads it only on SIGHUP); use a directory with "
                "--dhcp-hostsdir to need no reload at all"
            )

        if self.reload_command == "signal":
            if self.host:
                raise PixieConfigError(
                    "dnsmasq: reload=signal is local only; give a command to run "
                    "over ssh instead"
                )
            _signal_pidfile(self.pidfile)
            return

        argv = _shlex.split(self.reload_command)
        if self.host:
            destination = f"{self.user}@{self.host}" if self.user else self.host
            argv = ["ssh", destination, *argv]
        LOGGER.debug("dnsmasq reload: %s", argv)
        _subprocess.run(argv, check=True, timeout=60)


def _require_sftp(uri: str) -> None:
    """Fail with netboot's own extra name before pathlib_next names its own.

    pathlib_next reaches `sftp://` through paramiko (or asyncssh), neither of
    which netboot installs; its message names *its* extras, which is not what an
    operator of netboot has to install.
    """
    if not uri.startswith("sftp://"):
        return
    import importlib.util as _util

    if _util.find_spec("paramiko") is None and _util.find_spec("asyncssh") is None:
        raise ImportError(
            "a remote dnsmasq path (sftp://) requires netboot's 'ssh' extra: "
            "pip install netboot[ssh]"
        )


def _tag(target) -> str:
    """A dnsmasq tag for this target: its id, reduced to what a tag may hold."""
    raw = str(getattr(target, "_id", "") or "")
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
    return cleaned.strip("-") or "netboot"


def _host_line(target, tag: str) -> str:
    from .. import PixieLookupError
    from ..engine import PixieTarget

    mac = str(target.mac)
    if not mac or mac == PixieTarget._NULL_MAC:
        raise PixieLookupError(
            f"target {target._id!r} has no MAC address, and dnsmasq keys "
            "reservations by MAC; give the target a mac, or key it by one"
        )
    fields = [mac, f"set:{tag}"]
    if target.ip:
        fields.append(str(target.ip))
    if target.hostname:
        fields.append(target.hostname)
    return "dhcp-host=" + ",".join(fields)


def _option_lines(tag: str, options) -> "list[str]":
    """Translate the generic options into dnsmasq's own syntax."""
    lines: "list[str]" = []
    boot_file = options.get("boot-file-name")
    next_server = options.get("next-server")
    if boot_file or next_server:
        # next-server is not an option in dnsmasq: it is dhcp-boot's third field.
        lines.append(
            f"dhcp-boot=tag:{tag},{_value(boot_file) if boot_file else ''},,"
            f"{_value(next_server) if next_server else ''}".rstrip(",")
        )
    for name, value in options.items():
        if name in ("boot-file-name", "next-server"):
            continue
        code = _CODES.get(name)
        if code is None:
            code = name[len("option-") :] if name.startswith("option-") else name
        lines.append(f"dhcp-option=tag:{tag},{code},{_value(value)}")
    lines.extend(options.raw_for("dnsmasq"))
    return lines


def _value(value) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _is_directory(path) -> bool:
    """Directory mode: an existing directory, or a path written with a slash."""
    text = str(path)
    if text.endswith(("/", "\\")):
        return True
    try:
        return path.is_dir()
    except (NotImplementedError, OSError):  # pragma: no cover - exotic path types
        return False


def _atomic_write(path, text: str) -> None:
    """Write via a sibling temporary file where the path type allows a rename.

    A half-written `dhcp-host` line read on an unrelated SIGHUP is a lease handed
    to the wrong machine.
    """
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotImplementedError):
        pass
    tmp = parent / (path.name + ".netboot-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        _replace(tmp, path)
    except (NotImplementedError, AttributeError, OSError) as exc:
        LOGGER.debug("atomic write unavailable (%s); writing in place", exc)
        path.write_text(text, encoding="utf-8")


def _replace(tmp, path) -> None:
    """Rename `tmp` onto `path`, for whichever path flavour we have."""
    try:
        _os.replace(tmp.__fspath__(), path.__fspath__())
        return
    except (NotImplementedError, AttributeError):
        pass
    rename = getattr(tmp, "replace", None) or getattr(tmp, "rename", None)
    if rename is None:
        raise NotImplementedError("path type cannot rename")
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    rename(path)


def _signal_pidfile(pidfile: str) -> None:
    """SIGHUP the dnsmasq whose pid is in `pidfile` (local only)."""
    import signal

    with open(pidfile, encoding="utf-8") as handle:
        pid = int(handle.read().strip())
    _os.kill(pid, getattr(signal, "SIGHUP", signal.SIGTERM))
