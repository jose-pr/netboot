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
