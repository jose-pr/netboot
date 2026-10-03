"""What a finished target's DHCP entry becomes.

A target that still receives `boot-file-name` after its install finishes **boots
the installer again**. Removing the reservation is one way out and netboot's
default, but it throws away the fixed address, which is often the one thing the
rest of the estate depends on. So `dhcp_complete:` chooses:

```yaml
images:
  debian:
    dhcp_options: {boot-file-name: undionly.kpxe}
    dhcp_complete:
      keep: true                                  # default false = remove it
      options: {boot-file-name: sanboot.ipxe}     # omit for "no boot options"
```

* **`keep: false`** (the default) is netboot's original behaviour exactly: the
  reservation goes.
* **`keep: true`** with no `options` leaves the address and removes the boot
  options, so the client's firmware falls through to local disk.
* **`keep: true`** with `options` serves those instead -- an iPXE script ending in
  `sanboot` hands control to the disk, which works on firmware that has no
  local-disk fallback of its own.

Only the **boot** options are touched (`BOOT_OPTIONS`). Router, DNS and subnet
mask are what make a reservation worth keeping, and they are not what re-kicks a
machine.
"""

from __future__ import annotations

from .options import DhcpOptions, is_option

#: The options that make a client try to netboot. These are replaced or removed
#: on completion; everything else about the reservation stays as it was.
BOOT_OPTIONS = ("boot-file-name", "next-server", "tftp-server-name")


class DhcpCompletion:
    """What to do with this target's entry when provisioning finishes."""

    __slots__ = ("keep", "options")

    def __init__(self, keep: bool = False, options=None) -> None:
        self.keep = bool(keep)
        self.options = DhcpOptions(options or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DhcpCompletion(keep={self.keep}, options={dict(self.options)!r})"

    def __bool__(self) -> bool:
        """True when completion does anything other than remove the entry."""
        return self.keep

    def apply_to(self, options: DhcpOptions) -> DhcpOptions:
        """The options a kept entry should end up with.

        The boot options are dropped and `self.options` put in their place --
        **replaced, not merged**: the point is to stop serving the installer, and
        a merge would leave it there.
        """
        kept = DhcpOptions(
            {k: v for k, v in options.items() if k not in BOOT_OPTIONS},
            raw=options.raw,
        )
        kept.update(self.options)
        return kept


def parse_completion(declared, source) -> "DhcpCompletion|None":
    """Build a `DhcpCompletion` from a `dhcp_complete:` block, or raise."""
    from .. import PixieConfigError

    where = getattr(source, "_id", source)
    if declared is None:
        return None
    if not isinstance(declared, dict):
        raise PixieConfigError(
            f"dhcp_complete on {where!r} must be a mapping with `keep` and "
            f"`options`, got {type(declared).__name__}"
        )
    unknown = set(declared) - {"keep", "options"}
    if unknown:
        raise PixieConfigError(
            f"dhcp_complete on {where!r} has unknown key(s) "
            f"{', '.join(sorted(unknown))}; it takes `keep` and `options`"
        )
    options = declared.get("options") or {}
    if not isinstance(options, dict):
        raise PixieConfigError(
            f"dhcp_complete.options on {where!r} must be a mapping, got "
            f"{type(options).__name__}"
        )
    for key in options:
        if not is_option(key):
            raise PixieConfigError(
                f"dhcp_complete.options on {where!r} has unknown dhcp option {key!r}"
            )
    keep = declared.get("keep", False)
    if not isinstance(keep, bool):
        raise PixieConfigError(
            f"dhcp_complete.keep on {where!r} must be true or false, got " f"{keep!r}"
        )
    if options and not keep:
        # Serving options to an entry that is about to be deleted cannot work,
        # and quietly ignoring half a config is how a machine keeps reinstalling.
        raise PixieConfigError(
            f"dhcp_complete on {where!r} sets `options` but not `keep: true`; "
            f"options for a removed reservation have nowhere to go"
        )
    return DhcpCompletion(keep=keep, options=options)


def build_completion(ctx) -> DhcpCompletion:
    """How this target completes: zone, then image, then target -- later wins."""
    completion = DhcpCompletion()
    for source in (ctx.dhcpzone, ctx.image, ctx.target):
        if source is None:
            continue
        declared = parse_completion(getattr(source, "dhcp_complete", None), source)
        if declared is not None:
            completion = declared
    return completion
