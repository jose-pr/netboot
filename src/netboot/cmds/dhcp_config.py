"""Print — or apply — the server-side configuration a netboot config needs.

Some things a DHCP server needs cannot be created by the account netboot arms
with: a Windows policy on a host without the PowerShell module, a Kea client class
with no `class_cmds` hook, a dhcpd group that should survive a restart. netboot
knows exactly what each one is, because it is the same text its errors carry.

So this command prints it, and `--apply` runs it. Printing touches nothing, which
makes it safe to run, to read, and to paste into a change ticket. Applying is the
**only** place netboot may prompt for a credential: `pixie initiate` runs
unattended from hooks and provisioning systems, and a prompt there would hang a
pipeline rather than ask anyone.

Run it once per condition, by someone who holds the rights. After that netboot
finds the construct by name and needs none of them.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from netboot import Pixie
from netboot.dhcp.conditions import ConditionMissing, ConditionUnsupported
from netboot.logging import LOGGER


def register(parser: argparse.ArgumentParser, args) -> None:
    """Add this command's arguments to its subparser."""
    parser.add_argument(
        "target",
        nargs="?",
        help="Id, hostname, MAC or IP of one target; every target if omitted",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run the configuration instead of printing it (needs the rights)",
    )
    parser.add_argument(
        "--as",
        dest="run_as",
        metavar="USER",
        help=(
            "Account to apply as, prompting for its password. Implies --apply. "
            "Only usable on a terminal; unattended runs set "
            "PIXIE_WINDHCP_PASSWORD instead"
        ),
    )


def run(netboot: Pixie, args, conf: dict) -> int:
    """Print what every server needs; with `--apply`, make it so."""
    targets = _targets(netboot, args)
    if targets is None:
        return 1

    apply = bool(args.apply or args.run_as)
    if args.run_as and not _prompt_password(args.run_as):
        return 1

    printed = 0
    failures = 0
    for target in targets:
        ctx = netboot.make_context(target, require_image=False)
        zone = ctx.dhcpzone
        if zone is None:
            continue
        conditions = list(_conditions(ctx).values())
        if not conditions:
            continue
        for server in getattr(zone, "dhcpservers", None) or []:
            for condition in conditions:
                printed += 1
                if not apply:
                    _print(server, condition, server.condition_recipe(ctx, condition))
                    continue
                try:
                    server.ensure_condition(ctx, condition)
                except ConditionUnsupported as exc:
                    # Nothing to apply here, ever. Say so and keep going.
                    LOGGER.warning("%s", exc)
                    failures += 1
                except ConditionMissing as exc:
                    # Applying is what this command is for, so this means the
                    # rights or the hook are missing even now.
                    LOGGER.error("%s", exc)
                    failures += 1
                else:
                    LOGGER.info(
                        "%s: %s is in place on %s",
                        target._id,
                        condition.name,
                        getattr(server, "uri", server),
                    )
    if not printed:
        LOGGER.info("No conditional options are configured, so nothing is needed.")
    return 1 if failures else 0


def _targets(netboot: Pixie, args):
    """The targets to report on, or None when the named one cannot be found."""
    if not getattr(args, "target", None):
        return list(netboot.targets.values())
    try:
        target = netboot.lookup_target(args.target)
    except LookupError as exc:  # ambiguous: refuse rather than guess
        LOGGER.error("%s", exc)
        return None
    if target is None:
        LOGGER.error("Target not found: %s", args.target)
        return None
    return [target]


def _conditions(ctx):
    from netboot.dhcp.conditions import build_conditions

    return build_conditions(ctx)


def _print(server, condition, recipe: str) -> None:
    """One recipe, with a header naming where it belongs."""
    uri = getattr(server, "uri", server)
    print(f"# {uri} -- dhcp_when.{condition.name}")
    print(recipe or "# (nothing to configure for this backend)")
    print()


def _prompt_password(user: str) -> bool:
    """Ask for `user`'s password and put it where the backends read it.

    `getpass` only, and only on a terminal: without one this fails naming the
    environment variable rather than blocking a pipeline on a prompt nobody will
    answer. The value lives in this process's environment and is never logged,
    written, or interpolated into a generated script -- the backends pass it as
    data, which is the rule the whole windhcp script follows.
    """
    from netboot.dhcp.windhcp import PASSWORD_ENV_VAR

    if not sys.stdin.isatty():
        LOGGER.error(
            "--as needs a terminal to prompt on; set %s for an unattended run",
            PASSWORD_ENV_VAR,
        )
        return False
    password = getpass.getpass(f"Password for {user}: ")
    if not password:
        LOGGER.error("No password given, so nothing was applied.")
        return False
    os.environ[PASSWORD_ENV_VAR] = password
    os.environ.setdefault("PIXIE_WINDHCP_USER", user)
    return True
