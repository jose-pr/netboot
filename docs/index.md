# netboot

**PXE provisioning management.** Describe your netboot targets, images and DHCP
zones in config, and let `netboot` render the per-target boot artifacts and arm (or
disarm) DHCP for a machine as it enters and leaves the install process.

netboot is a small, hook-driven engine. A YAML config declares:

- **targets** — a machine, keyed by hostname / MAC / IP;
- **images** — what a target boots;
- **dhcp zones** — the network a target lives on, and the DHCP backend(s) for it;
- **repos / resources** — where boot artifacts are fetched from and served.

For a given target netboot builds a render **context** and produces netboot files
from templates — Jinja2 and shell-style built in; mako, Liquid, Handlebars,
Mustache, ERB and EPP through [tplx](https://github.com/jose-pr/tplx) — resolving
template names by MAC, hostname or IP with sensible fallbacks. Behaviour is extensible through an event-hook
system and pluggable DHCP backends (from [dhcpctl](https://github.com/jose-pr/dhcpctl)).

## Install

```sh
pip install netboot            # the library
pip install netboot[config]    # ...plus what the `pixie` CLI needs to read a config
# or, from a checkout:
pip install .
```

| Extra | Enables |
| ----- | ------- |
| `netboot[config]` | YAML config loading for the CLI (`pyyaml`) |
| `netboot[mako]` | `*.mako` templates (`tplx[mako]`) |
| `netboot[liquid]` | `*.liquid` templates (`tplx[liquid]`) |
| `netboot[handlebars]` | `*.hbs` / `*.handlebars` templates (`tplx[handlebars]`) |
| `netboot[mustache]` | `*.mustache` templates (`tplx[mustache]`) |
| `netboot[dns]` | The `dnspython` resolver backend (best coverage; without it hostname targets still resolve via the system/`nslookup` fallbacks) |
| `netboot[kea]` | The `kea://` DHCP backend (`requests`) |
| `netboot[dhcpd]` | The `dhcpd://` DHCP backend over OMAPI (`pypureomapi`) |
| `netboot[winrm]` | `windhcp://` over WinRM (`hostctl[winrm]`) |
| `netboot[ssh]` | `windhcp://` over ssh, and `dnsmasq://` with remote `sftp://` paths and reload (`hostctl[ssh]`) |
| `netboot[docs]`   | Build this documentation site (`mkdocs`) |

Built on [`duho`](https://github.com/jose-pr/duho) (CLI/args/command discovery),
[`pathapi`](https://github.com/jose-pr/pathapi) and `uripath` (URI-aware paths),
[`anycfg`](https://github.com/jose-pr/anycfg) (layered YAML),
[`tplx`](https://github.com/jose-pr/tplx) (templates) and
[`dhcpctl`](https://github.com/jose-pr/dhcpctl) (DHCP servers). IP/MAC/DNS
helpers come from
[`netimps`](https://pypi.org/project/netimps/).

## Where next

- [CLI](cli.md) — the `initiate` / `complete` commands and global options.
- [Configuration](configuration.md) — the shape of the YAML config.
- [Extending](extending.md) — hooks and custom DHCP backends.
- [API Reference](api.md) — the `Pixie` engine and context objects.
