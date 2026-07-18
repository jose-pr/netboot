# pixy

**PXE provisioning management.** Describe your netboot targets, images and DHCP
zones in config, and let `pixy` render the per-target boot artifacts and arm (or
disarm) DHCP for a machine as it enters and leaves the install process.

pixy is a small, hook-driven engine. A YAML config declares:

- **targets** — a machine, keyed by hostname / MAC / IP;
- **images** — what a target boots;
- **dhcp zones** — the network a target lives on, and the DHCP backend(s) for it;
- **repos / resources** — where boot artifacts are fetched from and served.

For a given target pixy builds a render **context** and produces netboot files
from Jinja2 or shell-style templates, resolving template names by MAC, hostname
or IP with sensible fallbacks. Behaviour is extensible through an event-hook
system and pluggable `DhcpServer` backends.

## Install

```sh
pip install pixy            # once published
# or, from a checkout:
pip install .
```

| Extra | Enables |
| ----- | ------- |
| `pixy[config]` | YAML config loading for the CLI (`pyyaml`) |
| `pixy[docs]`   | Build this documentation site (`mkdocs`) |

Built on [`duho`](https://github.com/jose-pr/duho) (CLI/args/command discovery),
[`pathlib_next`](https://github.com/jose-pr/pathlib_next) (URI-aware paths),
[`netutils`](https://github.com/jose-pr/netutils) (IP/MAC helpers),
[`yaconfiglib`](https://github.com/jose-pr/yaconfiglib) (layered YAML), and Jinja2.

## Where next

- [CLI](cli.md) — the `initiate` / `complete` commands and global options.
- [Configuration](configuration.md) — the shape of the YAML config.
- [Extending](extending.md) — hooks and custom `DhcpServer` backends.
- [API Reference](api.md) — the `Pixy` engine and context objects.
