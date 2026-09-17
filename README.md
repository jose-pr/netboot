# netboot

[![PyPI](https://img.shields.io/pypi/v/netboot.svg)](https://pypi.org/project/netboot/)
[![Python versions](https://img.shields.io/pypi/pyversions/netboot.svg)](https://pypi.org/project/netboot/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/netboot/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/netboot/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/netboot/test.yml)](https://github.com/jose-pr/netboot/actions/workflows/test.yml)

PXE provisioning management: describe your netboot targets, images and DHCP
zones in config, and let `netboot` render the per-target boot artifacts and arm (or
disarm) DHCP for a machine as it enters and leaves the install process.

`netboot` is a small, hook-driven engine. A YAML config declares **targets**
(host/MAC/IP), **images** (what to boot), **dhcp zones** (the network a target
lives on) and content **repos** (where artifacts are fetched/served from). For a
given target `netboot` builds a render **context** and produces netboot files from
Jinja2 or shell-style templates, resolving names by MAC, hostname or IP with
sensible fallbacks. Backends and behaviour are extensible through an event-hook
system and pluggable `DhcpServer` handlers.

## Install

```sh
pip install netboot            # the library
pip install netboot[config]    # ...plus what the `pixie` CLI needs to read a config
# or, from a checkout:
pip install .
```

Optional extras:

| Extra | Enables |
| ----- | ------- |
| `netboot[config]` | YAML config loading for the CLI (`pyyaml`) |
| `netboot[dns]` | The `dnspython` resolver backend (best coverage; without it hostname targets still resolve via the system/`nslookup` fallbacks) |
| `netboot[http]` | `http`/`https` repository services (`requests`); `file`/`tftp` repos and rendering need nothing extra |
| `netboot[docs]`   | Build the documentation site (`mkdocs`) |

Built on [`duho`](https://github.com/jose-pr/duho) (CLI/args/command discovery),
[`pathlib_next`](https://github.com/jose-pr/pathlib-next) (URI-aware paths),
[`yaconfiglib`](https://github.com/jose-pr/yaconfiglib) (layered YAML), and
Jinja2. IP/MAC/DNS helpers come from
[`netimps`](https://pypi.org/project/netimps/), re-exported as
`netboot.utils.net`.

## CLI

The command is `pixie` (PXE is pronounced "pixie"); `netboot` is the library
package it drives.

```sh
# Initiate the PXE process for a target (render artifacts + arm DHCP):
pixie initiate my-host

# Complete it (post-boot cleanup, disarm DHCP):
pixie complete my-host
```

Global options:

| Option | Purpose |
| ------ | ------- |
| `-c, --config PATH` | Explicit config file (yaml/cfg) |
| `--baseconfig DIR`  | Base config directory to search (default `./config`) |
| `-l, --load-module M` | Import module(s) before building netboot (config/hook deps) |
| `--cmdspath PATH` | Extra directories/packages to search for commands |

A target is looked up by exact id, or by hostname prefix / MAC / IP.

## Library

```python
from netboot import Pixie

pixie = Pixie(**config)                  # config: the merged YAML mapping
target = pixie.lookup_target("my-host")
ctx = pixie.make_context(target)         # render context (image + dhcpzone + target)
print(ctx.render("boot.ipxe.j2"))        # render a template against the context
pixie.initialize(target)                 # arm DHCP for the target
pixie.complete(target)                   # cleanup once installed
```

## Extending

- **Hooks.** Pass `hooks=[...]` (callables or `"module.func"` import strings) to
  `Pixie(...)`. Each hook `f(event, netboot, value, kwargs) -> value` is called for
  every `PixieEvent` and may transform the value flowing through it — used to
  customise lookup, context construction and the init/complete lifecycle.
- **DHCP backends.** Subclass `netboot.dhcp.DhcpServer`; the lowercased class name
  is the URI scheme it handles (`class dnsmasq(DhcpServer)` → `dnsmasq://...`).
  Import your plugin module via `--load-module` so it is registered before the
  config builds the zones.

A complete runnable setup — config, both template engines and a DHCP plugin —
is in [`examples/`](https://github.com/jose-pr/netboot/tree/main/examples).

## Development

```sh
python -m venv .venv/3.14
.venv/3.14/bin/pip install -e ".[dev,docs,config,http]"   # Scripts/ on Windows
.venv/3.14/bin/python -m pytest -q -rs                    # the suite
.venv/3.14/bin/python -m black src tests benchmarks       # formatter
.venv/3.14/bin/python -m mkdocs build --strict            # docs
```

Install every extra that has tests — without `http` the repository-service tests
skip instead of running, which `-rs` makes visible. Develop on the latest Python
and also run the floor (3.9) before finishing a piece of work. More detail, plus
the layout and CI notes, in
[`AGENTS.md`](https://github.com/jose-pr/netboot/blob/main/AGENTS.md).

## Releasing

Tags and `CHANGELOG.md` use SemVer; `pyproject.toml` uses PEP 440. Pre-1.0 the
minor slot is reserved for breaking the documented API — additions and fixes are
patches. Pushing a `v*` tag runs test → build → GitHub release → PyPI and then
deploys the docs for that tag; the release body is scraped from the matching
`## [x.y.z]` changelog section.

## License

MIT — see [LICENSE](https://github.com/jose-pr/netboot/blob/main/LICENSE).
