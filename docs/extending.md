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

An engine is a class with two things: the suffixes it claims (`EXT`) and
`render()`. Register it and it joins the engines every `Loader` consults.

```python
from netboot.templates import Template, register_template_type

@register_template_type
class MakoTemplate(Template):
    EXT = ".mako"                      # a string, or a sequence of them

    def __init__(self, template: str) -> None:
        self._source = template

    def render(self, **extras):
        ctx = self._globals_["ctx"]    # the PixieContext being rendered
        return my_mako_render(self._source, ctx)
```

Import the module before anything renders — `--load-module your.plugin`, the same
flag a DHCP backend plugin needs.

**Priority, not registration order, decides who gets a file.** Engines are
consulted highest `PRIORITY` first, and a plugin is necessarily registered
*after* the shipped engines — if order decided, every plugin would sit behind the
catch-all `CopyTemplate` and never see a file. So:

| `PRIORITY` | Meaning |
| ---------- | ------- |
| `DEFAULT_PRIORITY` (`0`) | an ordinary engine claiming its own suffixes — the default, and enough to outrank the copy engine |
| above `0` | override a shipped engine on a suffix it also claims (`priority=10` to take `.j2` from `JinjaTemplate`) |
| `FALLBACK_PRIORITY` (`-100`) | a last resort, where `CopyTemplate` sits |

Engines of *equal* priority keep the order they were given, so an explicit
`Loader(..., template_types=[...])` still means what it says. Set the priority on
the class, or at registration for a class you do not own:

```python
register_template_type(SomeonesEngine, priority=10)
```

`unregister_template_type(cls)` removes one again, and a `Loader` snapshots the
registry when it is built, so importing a plugin halfway through a run never
changes an engine already in use.

Other attributes on the contract: **`BINARY = True`** to be handed the file's raw
`bytes` instead of decoded text (and to return bytes, as `CopyTemplate` does),
and `EXT = None` to claim *every* suffix — which only makes sense together with a
low priority.

The shipped engines are the worked examples, and they cover the three shapes an
engine takes:

- **evaluates Python** (`JinjaTemplate`, `MakoTemplate`) — hand the engine `ctx`
  itself and let the template reach into it.
- **reads data only** (`LiquidTemplate`, `HandlebarsTemplate`,
  `MustacheTemplate`) — build the namespace with
  `netboot.templates.template_data(self._globals_, extras)`, which returns a lazy
  mapping view of the context. liquid cannot traverse attributes at all, so this
  is not optional for that class of engine.
- **shells out** (`ERBTemplate`, `EppTemplate`) — subclass
  `SubprocessTemplate`, set `COMMAND`, `ENV_VAR` and `EXT`, and implement
  `command(program, template_path, values_path)`. The base marshals the context
  with `jsonable()`, writes template and values into a temporary directory, runs
  the program with a timeout, and turns a non-zero exit into a
  `TemplateEngineError` carrying the program's own stderr.

An engine that needs an optional library imports it in a helper, not at module
scope, and raises `ImportError` naming the extra — that way the class can be
registered unconditionally, and a file it claims reports what is missing rather
than falling through to the copy engine.

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
