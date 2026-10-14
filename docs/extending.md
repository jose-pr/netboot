# Extending

## Event hooks

Pass `hooks=[...]` to `Pixie(...)` — each entry is a callable or a
`"module.function"` import string. A hook

```python
def my_hook(event, netboot, value, kwargs):
    ...
    return value
```

is invoked for every `PixieEvent` and may transform the value flowing through it.
Events fire around object creation (`NewPixieObject`, `SetPixieProperty`,
`PixieInitiated`), lookup (`LookupTarget`, `FoundTarget`, `FoundTargetImage`,
`FoundTargetDhcpzone`), context construction (`PixieContextForTarget`), and the
init/complete lifecycle (`StartPixieInitialize` … `EndPixieComplete`). This is the
seam for customising how targets/images/zones are resolved and how the render
context is assembled.

### The contract

- **Always return.** Every hook is handed the previous hook's return value, and
  the last return value is what netboot uses. A hook that falls off the end
  returns `None`, which is then what the engine gets — a lookup hook that
  forgets to `return value` turns every lookup into "not found".
- **The signature is positional**, and `kwargs` arrives as a plain `dict` (the
  fourth argument), not as `**kwargs`.
- **`netboot` is `None` for `NewPixieObject`**, which fires before the instance
  exists; `value` there is the class about to be instantiated, and returning a
  subclass is how you swap in your own.
- **`value` and `kwargs` differ per event.** `SetPixieProperty` gets a
  `(name, value)` tuple plus `origin=`/`rawvalue=`; the lookup events get the
  object found (or the query, for `LookupTarget`) plus `target=`; the lifecycle
  events get the target, then the built context.
- **`PixieEvent` is a string enum whose values are prefixed** — the value of
  `PixieEvent.LookupTarget` is the string `"PixieEvent.LookupTarget"`. Compare
  against the enum member, not a bare name.

```python
from netboot import PixieEvent

def only_on_lookup(event, netboot, value, kwargs):
    if event is not PixieEvent.FoundTarget:
        return value                      # pass everything else through
    return value or fallback_target()
```

## Custom template engines

Templates are rendered by [`tplx`](https://jose-pr.github.io/tplx/), so an engine
is a `tplx.Template` subclass: the suffixes it claims (`EXT`) and
`render_values()`. Register it and every render, includes included, can use it.

```python
import tplx

@tplx.register_engine
class ShoutyTemplate(tplx.Template):
    EXT = ".shouty"                     # a string, or a sequence of them

    def render_values(self, values):
        # `values` holds `ctx` and each of its fields (`target`, `image`, ...).
        return self.source.upper()
```

Import the module before anything renders — `--load-module your.plugin`, the same
flag a DHCP backend plugin needs. A package can instead declare the class under
the `tplx.engines` entry-point group, and installing it is enough.

**Priority, not registration order, decides who gets a file**: a plugin is
registered after the shipped engines, so `DEFAULT_PRIORITY` (`0`) already puts it
ahead of the catch-all copy engine, and `register_engine(cls, priority=10)` takes
a suffix from a shipped engine. `BINARY = True` hands the engine the file's raw
bytes. tplx documents the whole contract, the data helpers an engine for a data
language or an external program uses, and `tplx.testing.EngineContract`, the
test suite every engine passes.

## Custom DHCP backends

`netboot.dhcp.DhcpServer` dispatches on the URI scheme of a zone's `dhcpservers`
entry: a subclass whose lowercased class name equals the scheme handles it.

```python
from netboot.dhcp import DhcpServer

class dnsmasq(DhcpServer):        # handles dnsmasq://...
    def add_target(self, ctx):
        ...                       # arm DHCP for ctx.target
    def remove_target(self, ctx):
        ...                       # disarm it
```

netboot ships four backends — `netboot.dhcp.dnsmasq`, `.kea`, `.dhcpd` and
`.windhcp` — and they are the worked examples: one writes files (locally or over
`sftp://`), one speaks a REST API, one a binary protocol, and one runs
PowerShell over ssh or WinRM. A backend gets its client options from
`self.options_for(ctx)` and translates them; see the configuration guide for
what an operator writes.

Subclassing at any depth is honoured, so a backend may share an intermediate
base. Import your plugin module before the config builds the zones — pass
`--load-module your.plugin` (repeat or colon-separate for several) so the
`DhcpServer` subclass is registered when `dnsmasq://...` is resolved.

An unknown scheme raises a clear `ValueError` rather than silently doing nothing.

### Extra commands and conditions

netboot models the options it can translate everywhere, and `extras()` is the way
in for everything else — an extra statement, an extra command, or a condition
that makes the server answer two kinds of client differently. The canonical case
is the **iPXE chainload**: hand a PXE ROM the iPXE binary, then hand iPXE itself
the script, or iPXE loads iPXE forever.

Two ways in, both ending up in the same list:

```yaml
dhcpservers:
  # from config, per connection
  - dhcpd://key@dhcp01/?raw.dhcpd=if exists user-class and option user-class = "iPXE" { filename "boot.ipxe"; } else { filename "undionly.kpxe"; }
```

```python
# from code, which is the one that can look at the target
from netboot.dhcp.dhcpd import dhcpd

class dhcpd(dhcpd):                      # still handles dhcpd://
    def extras(self, ctx, phase="add"):
        lines = super().extras(ctx, phase)   # keep the config's own fragments
        if phase == "add" and ctx.image.globals.get("ipxe"):
            lines.append(
                'if exists user-class and option user-class = "iPXE" '
                '{ filename "boot.ipxe"; } else { filename "undionly.kpxe"; }'
            )
        return lines
```

`phase` is `add` or `remove`. A bare `raw.<backend>=` belongs to `add`;
`raw.<backend>.remove=` is for a fragment that undoes something at teardown (a
policy created while arming, say). What a fragment *is* belongs to the backend:

| Backend | A fragment is | Where it lands |
| ------- | ------------- | -------------- |
| `dhcpd://` | dhcpd config **statements** | appended to the host object's `statements`, after the modelled options — so a conditional written there wins |
| `dnsmasq://` | config **lines** (`dhcp-match=set:ipxe,77,iPXE`, `dhcp-boot=tag:ipxe,boot.ipxe`) | the options file for that target's tag |
| `kea://` | an **`option-data` entry** | the reservation's `option-data` |
| `windhcp://` | a **PowerShell line**, or a `{"Args": [...], "Ignore": false}` **netsh command** | after the reservation and its options |

A netsh command works under either windhcp `method`: with `method=netsh` it joins
the command list, and with `method=powershell` it becomes the `netsh` call it
describes rather than being dropped. `Args` may be a list, or one string that is
split on whitespace — a query string has nowhere to put a list. `Ignore` marks a
command whose failure is acceptable, which is what a teardown usually wants.

Fragments are emitted **verbatim**: netboot does not parse, validate or escape
them. That is the point of an escape hatch, and its risk — a value that ends a
dhcpd statement early, or a PowerShell line that does more than it looks like,
is yours to get right. Where netboot builds the text itself (a netsh command
under `method=powershell`) it quotes it properly.

Order is defined: modelled options first, then `extras()`, in the order given
(unsuffixed `raw.<backend>` before `raw.<backend>.add`). A condition that
overrides an option therefore comes after the thing it overrides, which is what
both dhcpd's last-write-wins statements and a Windows policy need.

A declarative, cross-backend way to say "serve this file to iPXE and that one to
a PXE ROM" is not here yet, because the backends disagree about where a condition
lives — dhcpd puts it on the host, Windows in a scope-level policy, dnsmasq in a
tag, Kea in a client class. `extras()` is what makes it expressible today.

