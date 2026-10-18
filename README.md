# netboot

[![Version](https://img.shields.io/pypi/v/netboot.svg)](https://pypi.org/project/netboot/)
[![Python versions](https://img.shields.io/pypi/pyversions/netboot.svg)](https://pypi.org/project/netboot/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/netboot/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/netboot/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/netboot/test.yml)](https://github.com/jose-pr/netboot/actions/workflows/test.yml)

PXE provisioning management: describe your targets, images and DHCP zones in
config, and **netboot renders each machine's boot artifacts and arms (or
disarms) DHCP for it** as it enters and leaves the install. The command is
`pixie` (PXE is pronounced "pixie"); `netboot` is the engine it drives.
Documentation: <https://jose-pr.github.io/netboot/>.

## Features

- **One config, every machine** — **targets** (by hostname, MAC or IP),
  **images** (what to boot), **DHCP zones** (the network and its servers) and
  **repos** (where artifacts are served from), as layered YAML with `!include`.
- **Templates in any engine** — a target's artifacts are found by MAC, then
  hostname, then IP, then the bare name, and rendered by
  [tplx](https://github.com/jose-pr/tplx): Jinja and a `%{NAME}` shell engine
  built in, mako, Liquid, Handlebars, Mustache, ERB and EPP behind extras, and
  a byte-exact copy for anything else.
- **Four DHCP servers** through [dhcpctl](https://github.com/jose-pr/dhcpctl),
  each imported only when a config names it:

  | Scheme | Server | Install |
  | --- | --- | --- |
  | `dnsmasq://` | dnsmasq, hosts and options files | nothing locally; `netboot[ssh]` for `sftp://` paths |
  | `kea://`, `keas://` | ISC Kea control agent | `netboot[kea]` |
  | `dhcpd://` | ISC dhcpd over OMAPI | `netboot[dhcpd]` |
  | `windhcp://` | Windows DHCP, cmdlets or netsh | nothing locally; `netboot[ssh]` / `netboot[winrm]` remotely |

- **Conditional options** — an iPXE client can be served a different boot file
  from the ROM that chainloads it, as each server expresses that.
- **Completion that keeps the machine** — a finished target can keep its
  reservation without the installer's boot options.
- **Hooks on every step** — lookup, context building, DHCP options and the
  arm/disarm lifecycle each fire an event a hook can change.

## Installation

```sh
pip install netboot[config]    # the engine plus what `pixie` needs to read a config
```

| Extra | Enables |
| ----- | ------- |
| `netboot[config]` | YAML config loading for the CLI (`pyyaml`) |
| `netboot[dns]` | The `dnspython` resolver backend (without it hostname targets still resolve via the system/`nslookup` fallbacks) |
| `netboot[mako]` | `*.mako` templates (`tplx[mako]`) |
| `netboot[liquid]` | `*.liquid` templates (`tplx[liquid]`) |
| `netboot[handlebars]` | `*.hbs` / `*.handlebars` templates (`tplx[handlebars]`) |
| `netboot[mustache]` | `*.mustache` templates (`tplx[mustache]`) |
| `netboot[kea]` | The `kea://` DHCP backend |
| `netboot[dhcpd]` | The `dhcpd://` DHCP backend over OMAPI |
| `netboot[winrm]` | `windhcp://` over WinRM |
| `netboot[ssh]` | `windhcp://` over ssh, and `dnsmasq://` with remote `sftp://` paths and reload |
| `netboot[docs]`   | Build the documentation site |

`*.erb` and `*.epp` templates need no extra, but do need `ruby` or `puppet`
installed: they render through the real program. `TPLX_RUBY` / `TPLX_PUPPET`
name it when it is not on `PATH`. Python 3.9 or later.

## Quick start

`config/pixie.yaml`, which `pixie` finds in the working directory:

```yaml
targets:
  web01: {ip: 10.0.0.10, mac: "52:54:00:00:00:10", image: debian}
images:
  debian:
    template_path: [debian]
    dhcp_options: {boot-file-name: pxelinux.0, next-server: 10.0.0.2}
dhcpzones:
  lan:
    network: 10.0.0.0/24
    gateway: 10.0.0.1
    dhcpservers:
      - dnsmasq:///?hostsfile=/etc/dnsmasq.d/netboot/hosts/&optsfile=/etc/dnsmasq.d/netboot/opts/
```

`config/templates/debian/boot.cfg.j2`, which answers to `boot.cfg`:

```jinja
default install
label install
  kernel vmlinuz
  append ip={{ target.ip }} hostname={{ target.hostname }}
```

```sh
pixie render web01 boot.cfg      # what web01 would be served, from config/templates/debian/
pixie initiate web01             # arm DHCP for it
pixie complete web01             # disarm it once the install is done
```

The same from Python:

```python
import anycfg
from netboot import Pixie

config = anycfg.load("config/pixie.yaml")
# The CLI defaults the template root to the config's directory; here it is given.
pixie = Pixie(templates=["config/templates"], **config)
target = pixie.lookup_target("web01")
ctx = pixie.make_context(target)         # image + zone + target, and a renderer
print(ctx.render("boot.cfg"))
pixie.initialize(target)                 # arm DHCP
pixie.complete(target)                   # disarm it
```

A complete runnable setup — config, templates and a DHCP plugin that records
instead of arming — is in [`examples/`](https://github.com/jose-pr/netboot/tree/main/examples).

## Command line

```sh
pixie initiate <target> [--iscsi]          # arm DHCP; hooks may render and run more
pixie complete <target>                    # disarm DHCP
pixie render <target> NAME... [--out DIR]  # render artifacts, arming nothing
pixie dhcp-config [target] [--apply] [--as USER]   # what the servers need set up once
```

A target is named by id, hostname, MAC or IP, or a unique hostname prefix.
Global options: `-c/--config FILE`, `--baseconfig DIR` (default `./config`),
`-l/--load-module M` (import a plugin first), `--cmdspath PATH` (more
commands), `-v`/`-q`/`--loglevel`. Exit status 0 is success, 1 a target not
found or ambiguous, 2 a configuration that cannot be used, reported as one line.

## API overview

| Module | Purpose |
| --- | --- |
| `netboot` | `Pixie` (the engine), `PixieTarget`, `PixieImage`, `PixieContext`, `PixieEvent`, the errors |
| `netboot.dhcp` | DHCP zones and what a target is served: `DHCPZone`, `build_lease`, `reservation_for`, `arm`, `disarm` |
| `netboot.content` | `Repository` and `Resource`: where artifacts are served from |
| `netboot.exceptions` | `PixieError`, `PixieConfigError`, `PixieLookupError` |
| `netboot.cli` | the `pixie` command line, `main(argv=None, *, name=None) -> int`; one class per command |

## Extending

- **Hooks.** Pass `hooks=[...]` (callables or `"module.func"` import strings) to
  `Pixie(...)`, or list them under `hooks:`. Each hook
  `f(event, netboot, value, kwargs) -> value` is called for every `PixieEvent`
  and returns the value to pass on.
- **DHCP backends.** Subclass `dhcpctl.DHCPServer` and register it with
  `dhcpctl.register_backend(scheme, cls)` from a `--load-module` plugin, or the
  `dhcpctl.backends` entry point. A server URI says how to reach the server;
  what it tells a client comes from the zone, image and target.
- **Commands.** A module with `register(parser, args)` and
  `run(netboot, args, conf)`, or a duho class command, on `--cmdspath` is a
  `pixie` command.

## Development

```sh
python -m venv .venv/3.14
.venv/3.14/bin/pip install -e ".[dev,docs,config]"        # Scripts/ on Windows
.venv/3.14/bin/python -m pytest -q -rs                    # the suite
.venv/3.14/bin/python -m black src tests benchmarks       # formatter
.venv/3.14/bin/python -m mkdocs build --strict            # docs
```

`dev` carries every library a test needs (the DHCP backends' and the template
engines'), so only the `.erb`/`.epp` tests skip, unless `ruby`/`puppet` are
installed; `-rs` shows what skipped. Develop on the latest Python and also run
the floor (3.9) before finishing a piece of work. More detail, plus the layout
and CI notes, in [`AGENTS.md`](https://github.com/jose-pr/netboot/blob/main/AGENTS.md).

### Releasing

Tags and `CHANGELOG.md` use SemVer; `pyproject.toml` uses PEP 440. Pre-1.0 the
minor slot is reserved for breaking the documented API — additions and fixes are
patches. Pushing a `v*` tag runs test → build → GitHub release → PyPI and then
deploys the docs for that tag; the release body is scraped from the matching
`## [x.y.z]` changelog section.

## License

MIT — see [LICENSE](https://github.com/jose-pr/netboot/blob/main/LICENSE).
