# pixy

PXE provisioning management: describe your netboot targets, images and DHCP
zones in config, and let `pixy` render the per-target boot artifacts and arm (or
disarm) DHCP for a machine as it enters and leaves the install process.

`pixy` is a small, hook-driven engine. A YAML config declares **targets**
(host/MAC/IP), **images** (what to boot), **dhcp zones** (the network a target
lives on) and content **repos** (where artifacts are fetched/served from). For a
given target `pixy` builds a render **context** and produces netboot files from
Jinja2 or shell-style templates, resolving names by MAC, hostname or IP with
sensible fallbacks. Backends and behaviour are extensible through an event-hook
system and pluggable `DhcpServer` handlers.

## Install

```sh
pip install pixy            # once published
# or, from a checkout:
pip install .
```

Optional extras:

| Extra | Enables |
| ----- | ------- |
| `pixy[config]` | YAML config loading for the CLI (`pyyaml`) |
| `pixy[dns]` | DNS resolution of hostname targets (`dnspython`) |
| `pixy[docs]`   | Build the documentation site (`mkdocs`) |

Built on [`duho`](https://github.com/jose-pr/duho) (CLI/args/command discovery),
[`pathlib_next`](https://github.com/jose-pr/pathlib_next) (URI-aware paths),
[`yaconfiglib`](https://github.com/jose-pr/yaconfiglib) (layered YAML), and
Jinja2. IP/MAC/DNS helpers are vendored in-tree (`pixy._netutils`).

## CLI

```sh
# Initiate the PXE process for a target (render artifacts + arm DHCP):
pixy initiate my-host

# Complete it (post-boot cleanup, disarm DHCP):
pixy complete my-host
```

Global options:

| Option | Purpose |
| ------ | ------- |
| `-c, --config PATH` | Explicit config file (yaml/cfg) |
| `--baseconfig DIR`  | Base config directory to search (default `./config`) |
| `-l, --load-module M` | Import module(s) before building pixy (config/hook deps) |
| `--cmdspath PATH` | Extra directories/packages to search for commands |

A target is looked up by exact id, or by hostname prefix / MAC / IP.

## Library

```python
from pixy import Pixy

pixy = Pixy(**config)                 # config: the merged YAML mapping
target = pixy.lookup_target("my-host")
ctx = pixy.make_context(target)       # render context (image + dhcpzone + target)
print(ctx.render("boot.ipxe.j2"))     # render a template against the context
pixy.initialize(target)               # arm DHCP for the target
pixy.complete(target)                 # cleanup once installed
```

## Extending

- **Hooks.** Pass `hooks=[...]` (callables or `"module.func"` import strings) to
  `Pixy(...)`. Each hook `f(event, pixy, value, kwargs) -> value` is called for
  every `PixyEvent` and may transform the value flowing through it — used to
  customise lookup, context construction and the init/complete lifecycle.
- **DHCP backends.** Subclass `pixy.dhcp.DhcpServer`; the lowercased class name
  is the URI scheme it handles (`class dnsmasq(DhcpServer)` → `dnsmasq://...`).
  Import your plugin module via `--load-module` so it is registered before the
  config builds the zones.

## License

MIT — see [LICENSE](LICENSE).
