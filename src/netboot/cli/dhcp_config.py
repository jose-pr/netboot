"""`pixie dhcp-config`: print -- or apply -- what the DHCP servers need set up.

Some things a DHCP server needs cannot be created by the account netboot arms
with: a Windows policy on a host without the PowerShell module, a Kea client class
with no `class_cmds` hook, a dhcpd group OMAPI may not create. netboot
knows exactly what each one is, because it is the same text its errors carry.

So this command prints it, and `--apply` runs it. Printing touches nothing, which
makes it safe to run, to read, and to paste into a change ticket. Applying is the
only place netboot may prompt for a credential: `pixie initiate` runs
unattended from hooks and provisioning systems, and a prompt there would hang a
pipeline rather than ask anyone.
"""

from __future__ import annotations

import getpass
import sys
import typing as _ty

import dhcpctl

from ..dhcp import build_conditions, condition_recipe, connect, reservation_for
from ..exceptions import PixieLookupError
from ..logging import LOGGER
from ._common import Command

if _ty.TYPE_CHECKING:
    from .. import PixieContext, PixieTarget

__all__ = ["DhcpConfig"]


class DhcpConfig(Command):
    """Print the server-side setup the config's conditions need; --apply makes it so.

    Run it once per condition, by someone who holds the rights; after that
    netboot finds each construct by name and needs none of them.
    """

    _parsername_ = "dhcp-config"

    target: _ty.Optional[str] = None
    "Id, hostname, MAC or IP of one target; every target if omitted"
    ("target",)

    apply: bool = False
    "Run the configuration instead of printing it (needs the rights)"
    ("--apply",)

    run_as: _ty.Optional[str] = None
    (
        "Account to apply as, prompting for its password; implies --apply, and "
        "needs a terminal (an unattended run gives the server's user and "
        "password in its dhcpservers entry)"
    )
    ("--as",)

    def __call__(self) -> int:
        targets = self.targets()
        if targets is None:
            return 1
        secrets = None
        if self.run_as:
            password = _prompt_password(self.run_as)
            if password is None:
                return 1
            secrets = {"user": self.run_as, "password": password}
        printed = failures = 0
        shown: "set[tuple[str, str, str]]" = set()
        for target in targets:
            ctx = self.context(target)
            if ctx is None:
                continue
            conditions = list(build_conditions(ctx).values())
            if not conditions:
                continue
            for server in ctx.dhcpzone.dhcpservers or []:
                if secrets is not None:
                    server = self.as_account(server, secrets)
                    if server is None:
                        failures += 1
                        continue
                for condition in conditions:
                    printed += 1
                    if self.apply or secrets is not None:
                        failures += not self.ensure(ctx, server, condition)
                        continue
                    text = condition_recipe(ctx, server, condition)
                    # A condition targets share has one recipe; print it once.
                    key = (str(server), condition.name, text)
                    if key not in shown:
                        shown.add(key)
                        _print(server, condition, text)
        if not printed:
            LOGGER.info("No conditional options are configured, so nothing is needed.")
        return 1 if failures else 0

    def targets(self) -> "list[PixieTarget] | None":
        """The targets to report on, or None when the named one cannot be found."""
        if not self.target:
            return list(self.netboot.targets.values())
        target = self.find(self.target)
        return None if target is None else [target]

    def context(self, target: "PixieTarget") -> "PixieContext | None":
        """The target's context; with no target named, None for one without a zone."""
        try:
            return self.netboot.make_context(target, require_image=False)
        except PixieLookupError as exc:
            if self.target:
                raise
            # One target without a zone must not hide every target after it.
            LOGGER.warning("skipping %s: %s", target._id, exc)
            return None

    def as_account(
        self, server: dhcpctl.DHCPServer, secrets: "dict[str, str]"
    ) -> "dhcpctl.DHCPServer | None":
        """`server` again, applying as `secrets`' account, which overrides any user
        and password its URI carries; None when its backend takes no password."""
        if "password" not in type(server).URI_SECRETS.values():
            LOGGER.error("%s takes no password, so --as cannot apply there", server)
            return None
        return connect(server.uri, getattr(self.netboot, "option_codes", None), secrets)

    def ensure(
        self,
        ctx: "PixieContext",
        server: dhcpctl.DHCPServer,
        condition: dhcpctl.Condition,
    ) -> bool:
        """Apply `condition` on `server`; False, after saying why, if it failed."""
        try:
            server.ensure_condition(reservation_for(ctx, server), condition)
        except dhcpctl.ConditionUnsupportedError as exc:
            # Nothing to apply here, ever. Say so and keep going.
            LOGGER.warning("%s", exc)
            return False
        except dhcpctl.ConditionMissingError as exc:
            # Applying is what this command is for, so this means the rights
            # or the hook are missing even now.
            LOGGER.error("%s", exc)
            return False
        LOGGER.info("%s: %s is in place on %s", ctx.target._id, condition.name, server)
        return True


def _print(
    server: dhcpctl.DHCPServer, condition: dhcpctl.Condition, recipe: str
) -> None:
    """One recipe, with a header naming where it belongs."""
    # `str` of a server is its URI with any password masked.
    print(f"# {server} -- dhcp_when.{condition.name}")
    print(recipe or "# (nothing to configure for this backend)")
    print()


def _prompt_password(user: str) -> "str | None":
    """Ask for `user`'s password, or None.

    `getpass` only, and only on a terminal: without one this fails saying where
    an unattended run puts the password, rather than blocking a pipeline on a
    prompt nobody will answer. The value stays in memory: it is never logged,
    written, or interpolated into a generated script -- the backends pass it as
    data, which is the rule the whole windhcp script follows.
    """
    if not _console_stdin():
        LOGGER.error(
            "--as needs a terminal to prompt on; for an unattended run give the "
            "server's user and password in its dhcpservers entry"
        )
        return None
    password = getpass.getpass(f"Password for {user}: ")
    if not password:
        LOGGER.error("No password given, so nothing was applied.")
        return None
    return password


def _console_stdin() -> bool:
    """Is standard input a terminal someone can answer a prompt on?

    On Windows the NUL device reports `isatty()` too, and a prompt on it waits
    forever, so there the handle must also be a console's.
    """
    if not sys.stdin or not sys.stdin.isatty():
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt

    mode = ctypes.c_uint32()
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
