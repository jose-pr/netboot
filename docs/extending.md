# Extending

## Event hooks

Pass `hooks=[...]` to `Piskie(...)` — each entry is a callable or a
`"module.function"` import string. A hook

```python
def my_hook(event, piskie, value, kwargs):
    ...
    return value
```

is invoked for every `PiskieEvent` and may transform the value flowing through it.
Events fire around object creation (`NewPiskieObject`, `SetPiskieProperty`,
`PiskieInitiated`), lookup (`LookupTarget`, `FoundTarget`, `FoundTargetImage`,
`FoundTargetDhcpzone`), context construction (`PiskieContextForTarget`), and the
init/complete lifecycle (`StartPiskieInitialize` … `EndPiskieComplete`). This is the
seam for customising how targets/images/zones are resolved and how the render
context is assembled.

## Custom DHCP backends

`piskie.dhcp.DhcpServer` dispatches on the URI scheme of a zone's `dhcpservers`
entry: a subclass whose lowercased class name equals the scheme handles it.

```python
from piskie.dhcp import DhcpServer

class dnsmasq(DhcpServer):        # handles dnsmasq://...
    def add_target(self, ctx):
        ...                       # arm DHCP for ctx.target
    def remove_target(self, ctx):
        ...                       # disarm it
```

Subclassing at any depth is honoured, so a backend may share an intermediate
base. Import your plugin module before the config builds the zones — pass it via
`--load-module your.plugin` (or list it under the config's module loading) so the
`DhcpServer` subclass is registered when `dnsmasq://...` is resolved.

An unknown scheme raises a clear `ValueError` rather than silently doing nothing.
