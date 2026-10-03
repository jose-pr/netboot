"""Conditions: options a server hands out only to clients that match a test.

The case that drives this is the **iPXE chainload**. A PXE ROM must be handed the
iPXE binary, and iPXE itself must then be handed a script -- serve the binary to
both and iPXE loads iPXE forever. The discriminator is a DHCP option the client
sends (`user-class` is `iPXE`), so "it depends on who is asking" has to reach the
server, which no amount of per-target options can express.

Every server can do this, and no two do it the same way: Windows has a
scope-level **policy**, dhcpd a **group** of conditional statements (or an inline
`if`), Kea a **client class**. So a condition is declared once, under a **name**,
and each backend uses that name for its own construct:

```yaml
dhcp_when:
  ipxe:                                    # -> policy/group/class "ipxe"
    match: {user-class: iPXE}
    options: {boot-file-name: boot.ipxe}
```

Keying by name is what lets two targets share one policy instead of creating two,
and what lets a target override an image's condition by redeclaring the same key.

A backend that cannot express a condition **fails** rather than arming without
it: a target that silently misses its chainload boots the installer again, which
is worse than a warning. Arming is best effort per server, so one backend
refusing still leaves the others armed (see `DhcpServer.add_target` callers).
"""

from __future__ import annotations

import typing as _ty

from .options import DhcpOptions, is_option

#: `match` keys every backend can test, and the DHCP option each reads.
#: Deliberately small: a key here must be expressible as a Windows policy
#: condition, a dhcpd `if` and a Kea `test`, or it does not belong.
MATCH_KEYS = {
    "user-class": 77,
    "vendor-class": 60,
}

#: What a name may contain. A name reaches a PowerShell argument, a dhcpd config
#: identifier and a Kea class name, so it is kept to the intersection rather than
#: quoted three different ways.
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class DhcpCondition:
    """One named condition: a test, and the options to serve when it matches.

    `name` is the construct's name on every backend. `match` maps a key from
    `MATCH_KEYS` to the value the client must send; several keys mean "all of
    them", which each backend expresses in its own conjunction.
    """

    __slots__ = ("name", "match", "options")

    def __init__(self, name: str, match: dict, options: dict) -> None:
        self.name = name
        self.match = dict(match)
        self.options = DhcpOptions(options)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DhcpCondition({self.name!r}, match={self.match!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DhcpCondition):
            return NotImplemented
        return (
            self.name == other.name
            and self.match == other.match
            and dict(self.options) == dict(other.options)
        )

    @property
    def option_codes(self) -> "dict[int, object]":
        """The match as `{option code: value}`, for a backend that tests by code."""
        return {MATCH_KEYS[key]: value for key, value in self.match.items()}


def parse_conditions(declared, source) -> "dict[str, DhcpCondition]":
    """Build conditions from a `dhcp_when:` mapping, or raise naming the mistake.

    `source` is whatever declared them (an image, zone or target); it is only
    used in error messages, so a config mistake names the object to fix.
    """
    from .. import PixieConfigError

    where = getattr(source, "_id", source)
    if not declared:
        return {}
    if not isinstance(declared, dict):
        raise PixieConfigError(
            f"dhcp_when on {where!r} must be a mapping of name -> condition, got "
            f"{type(declared).__name__}; each key is the policy/group/class name"
        )

    conditions: "dict[str, DhcpCondition]" = {}
    for name, entry in declared.items():
        name = str(name)
        bad = set(name) - NAME_CHARS
        if not name or bad:
            raise PixieConfigError(
                f"dhcp_when name {name!r} on {where!r} may only contain letters, "
                f"digits, '-' and '_'"
                + (f" (found {''.join(sorted(bad))!r})" if bad else "")
            )
        if not isinstance(entry, dict):
            raise PixieConfigError(
                f"dhcp_when.{name} on {where!r} must be a mapping with `match` "
                f"and `options`, got {type(entry).__name__}"
            )
        unknown = set(entry) - {"match", "options"}
        if unknown:
            raise PixieConfigError(
                f"dhcp_when.{name} on {where!r} has unknown key(s) "
                f"{', '.join(sorted(unknown))}; it takes `match` and `options`"
            )
        match = entry.get("match") or {}
        if not isinstance(match, dict) or not match:
            raise PixieConfigError(
                f"dhcp_when.{name} on {where!r} needs a non-empty `match` "
                f"mapping; accepted keys: {', '.join(sorted(MATCH_KEYS))}"
            )
        for key in match:
            if key not in MATCH_KEYS:
                raise PixieConfigError(
                    f"dhcp_when.{name} on {where!r} cannot match on {key!r}; "
                    f"every backend must be able to test it, so the accepted "
                    f"keys are {', '.join(sorted(MATCH_KEYS))}"
                )
        options = entry.get("options") or {}
        if not isinstance(options, dict) or not options:
            raise PixieConfigError(
                f"dhcp_when.{name} on {where!r} needs a non-empty `options` "
                f"mapping -- a condition that serves nothing has no effect"
            )
        for key in options:
            if not is_option(key):
                raise PixieConfigError(
                    f"dhcp_when.{name} on {where!r} has unknown dhcp option " f"{key!r}"
                )
        conditions[name] = DhcpCondition(name, match, options)
    return conditions


def build_conditions(ctx) -> "dict[str, DhcpCondition]":
    """Every condition that applies to this target, layered by name.

    Zone, then image, then target -- later wins, **by name**, so a target
    redeclaring `ipxe` replaces the image's rather than adding a second one.
    """
    conditions: "dict[str, DhcpCondition]" = {}
    for source in (ctx.dhcpzone, ctx.image, ctx.target):
        if source is None:
            continue
        conditions.update(parse_conditions(getattr(source, "dhcp_when", None), source))
    return conditions


class ConditionUnsupported(Exception):
    """This backend cannot express a condition at all.

    Raised instead of arming without it. Not a `PixieError`: `netboot.dhcp` is
    imported *by* the engine, so the error cannot live there -- the engine
    catches it like any other arming failure and logs it per server.
    """


class ConditionMissing(Exception):
    """The construct is absent and this backend cannot create it.

    Carries `recipe`, the text a privileged operator must apply -- the whole
    point being that the error is actionable rather than a hint to go and read
    the server's documentation.
    """

    def __init__(self, message: str, recipe: str = "") -> None:
        super().__init__(message if not recipe else f"{message}\n\n{recipe}")
        self.recipe = recipe
