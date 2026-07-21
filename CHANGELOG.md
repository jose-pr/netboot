# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Breaking:** public classes renamed with a `Pixie` prefix (PXE is pronounced
  "pixie"): `Netboot` → `Pixie`, `NetbootTarget` → `PixieTarget`,
  `NetbootImage` → `PixieImage`, `NetbootContext` → `PixieContext`,
  `NetbootEvent` → `PixieEvent` (hook event string values change accordingly).
  The import package and config layout stay `netboot`.
- **Breaking:** the CLI is now `pixie` (the pronunciation of PXE); `netboot`
  remains only as the library/import package. Console script `netboot` →
  `pixie`, command-discovery env var `NETBOOT_PATH` → `PIXIE_PATH`, default
  config file `netboot.yaml` → `pixie.yaml`. `python -m netboot` still works.

## [0.1.0] - 2026-07-18

First packaged release, published as `netboot`.

### Added
- Packaged as `netboot` (src layout, hatchling, `netboot` console script, `py.typed`).
  Python 3.9+.
- PXE provisioning engine: `Netboot` with target/image/dhcpzone/repo lookup, a
  render `NetbootContext`, and an `initialize`/`complete` lifecycle.
- Event-hook system (`NetbootEvent`, `Netboot(hooks=...)`) for customising lookup,
  context construction and the init/complete lifecycle.
- Template rendering via a URI-aware Jinja2 loader plus a `%`-delimited shell
  template engine, selecting sources by MAC / hostname / IP.
- Pluggable `DhcpServer` backends dispatched by URI scheme, discovered
  recursively so plugin modules loaded via `--load-module` are honoured.
- CLI built on `duho`: `initiate` and `complete` commands with layered YAML
  config (`yaconfiglib`), command discovery, and `--load-module`/`--cmdspath`.
- Config objects use `yaconfiglib`'s `TypedNamespace` (`_parse_<field>` coercers)
  and `OpaqueMerge` (last-object-wins) so fully-built targets/zones with
  factory-function field hints are merged as opaque values (requires
  `yaconfiglib>=0.10.0`).
- Two-workflow CI (`test`, `Release`) and a MkDocs documentation site
  (`docs/` + `mkdocs.yml`, API reference via mkdocstrings), plus a
  `benchmarks/bench_netboot.py` micro-benchmark for the lookup/template hot paths.
- IP/MAC/DNS helpers vendored in-tree as `netboot._netutils`; DNS lookup of
  hostname targets is the optional `netboot[dns]` extra (`dnspython`).

### Changed
- Built on `duho` (args/command-discovery/app) rather than the in-house
  `coquilib` layer; IP/MAC helpers are vendored in-tree; the `sys.path`
  `vendor/` shim is gone. Version derives from installed package metadata.

### Fixed
- Target resolution no longer no-ops when the id is an IP address (`self.ip`
  self-assignment and a `resolve - True` typo).
- `utils.flatten` now produces a genuinely flat mapping and no longer raises
  `TypeError` on nested lists, so shell-template rendering works.
- `DhcpServer(uri)` raises a clear error for an unknown scheme instead of
  silently returning an inert base, and zone `dhcpservers` URIs are constructed
  into backends.
- `make_context` no longer deletes `globals` off shared image/dhcpzone/target
  objects, so a second target reusing an image keeps that image's globals.
- `_template_names` accepts the loader's template `**options`; template names
  stringify the target IP and skip an unspecified address.
- Command dispatch introspects each module's `run` signature, so a user command
  using duho's plain `run(args)` is dispatched via `duho.run_command`.
- Shell templates render `None` as empty instead of the literal `"None"`;
  config value construction no longer swallows non-`TypeError` errors; repo
  `joinpath` keeps `.local` a path so chained joins work.

[Unreleased]: https://github.com/jose-pr/netboot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jose-pr/netboot/releases/tag/v0.1.0
