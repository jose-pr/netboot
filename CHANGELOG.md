# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-07-21

Packaging/CI fixes only — no library or CLI behaviour changes.

### Fixed
- Release runs no longer fail at GitHub Release creation: the release is pinned
  to the tagged commit (`target_commitish`) instead of defaulting to the
  repository's default branch, which broke note generation for a release object
  still pointing at a pre-rename branch.
- The release workflow's docs job self-enables GitHub Pages (`enablement: true`
  plus `pages: write`), matching `docs.yml`, so a docs-site problem no longer
  turns an otherwise-successful release red.
- The docs-only workflow triggers on `main`; it was listening on `master`, a
  branch this repo does not have, so it never ran on a docs change.

### Documentation
- README: version/pythons/license/docs/CI badge row, and the install note names
  the `pixie` command instead of saying "once published".
- README library example binds the engine to `pixie` rather than `netboot`,
  which read as the package and left two calls referencing an undefined name.

First packaged release: the `netboot` library with the `pixie` command line.

### Added
- Packaged as `netboot` (src layout, hatchling, `pixie` console script, `py.typed`).
  Python 3.9+.
- PXE provisioning engine: `Pixie` with target/image/dhcpzone/repo lookup, a
  render `PixieContext`, and an `initialize`/`complete` lifecycle.
- Event-hook system (`PixieEvent`, `Pixie(hooks=...)`) for customising lookup,
  context construction and the init/complete lifecycle.
- Template rendering via a URI-aware Jinja2 loader plus a `%`-delimited shell
  template engine, selecting sources by MAC / hostname / IP.
- Pluggable `DhcpServer` backends dispatched by URI scheme, discovered
  recursively so plugin modules loaded via `--load-module` are honoured.
- `pixie` CLI built on `duho` (PXE is pronounced "pixie"; `netboot` is the
  library/import package): `initiate` and `complete` commands with layered YAML
  config (`config/pixie.yaml` via `yaconfiglib`), command discovery, and
  `--load-module`/`--cmdspath`.
- App settings read through `duho.env.Env("pixie")`, so `PIXIE_*` variables
  (notably `PIXIE_CMDS_PATH`) configure the CLI; the resolved accessor reaches
  commands as `args._env_`.
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

[Unreleased]: https://github.com/jose-pr/netboot/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/jose-pr/netboot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jose-pr/netboot/releases/tag/v0.1.0
