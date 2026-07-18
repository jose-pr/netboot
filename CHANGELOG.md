# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-18

First packaged release.

### Added
- Packaged as `pixy` (src layout, hatchling, `pixy` console script, `py.typed`).
  Python 3.9+.
- PXE provisioning engine: `Pixy` with target/image/dhcpzone/repo lookup, a
  render `PixyContext`, and an `initialize`/`complete` lifecycle.
- Event-hook system (`PixyEvent`, `Pixy(hooks=...)`) for customising lookup,
  context construction and the init/complete lifecycle.
- Template rendering via a URI-aware Jinja2 loader plus a `%`-delimited shell
  template engine, selecting sources by MAC / hostname / IP.
- Pluggable `DhcpServer` backends dispatched by URI scheme, discovered
  recursively so plugin modules loaded via `--load-module` are honoured.
- CLI built on `duho`: `initiate` and `complete` commands with layered YAML
  config (`yaconfiglib`), command discovery, and `--load-module`/`--cmdspath`.

### Changed
- Migrated the CLI off the in-house `coquilib` layer to `duho`
  (args/command-discovery/app) and onto the standalone `netutils` package for
  IP/MAC helpers; dropped the `sys.path` `vendor/` shim.

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

[Unreleased]: https://github.com/jose-pr/pixy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jose-pr/pixy/releases/tag/v0.1.0
