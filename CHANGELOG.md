# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `netboot[http]` extra. `http`/`https` repository services reach the network
  through `requests`, which nothing installed: `pathlib_next[uri]` does not
  depend on it, so `Repository.service("http")` failed with
  `ModuleNotFoundError: No module named 'requests'` on any install that did not
  happen to have it. Install `netboot[http]` for http-served repos; `file` and
  `tftp` repos, and rendering, need nothing extra. Without the extra those calls
  now raise `ImportError` naming it instead of failing inside the library.

### Security
- `shell_quote` now actually quotes. It wrapped values in `"` without escaping
  anything, so a value containing `` ` ``, `$`, `"` or `;` was expanded or
  executed by the shell that read the generated script — a password with `$` in
  it was silently corrupted, and a value taken from an inventory could run
  commands. It now defaults to POSIX single-quoting (`quote="'"`), escaping an
  embedded `'` as `'\''`; `quote='"'` escapes `"`, `\`, `` ` `` and `$`, and any
  other quote character raises `ValueError`. It also returns a `str` for a `str`
  input instead of always a one-element list, so `{{ shell_quote(v) }}` renders
  the value rather than `['"v"']`. Templates that relied on indexing the result
  of a scalar call must drop the `[0]`.

### Fixed
- Shell-engine templates render kickstart files. The engine substituted both
  `%{NAME}` and a bare `%name`, and treated an unknown bare one as an error, so
  any file containing `%packages`, `%pre`, `%post`, `%end` or `date +%Y` failed
  to render at all. Only the documented braced form `%{NAME}` is substituted
  now; a bare `%word` is literal text, including one that names a context
  variable. `%%` still yields a literal `%` and an unknown `%{NAME}` still
  raises. A template written with bare placeholders must brace them.
- `Pixie.globals` is really the deep copy the docs promised. The copy made in
  `__init__` was immediately overwritten by the annotated-attribute loop, so
  nested values stayed shared with the caller's config dict (mutating
  `engine.globals["a"]["b"]` changed the caller's mapping) and any global whose
  key started with `_` was silently dropped. Both are fixed: `_`-prefixed
  globals are ordinary variable names and are kept.
- `Pixie.lookup_dhcpzone` returns `None` instead of raising `AttributeError`
  when the target has no usable IP — the documented MAC-keyed target shape hit
  this on every `initiate` that did not name a `dhcpzone`.
- Template selection is deterministic. Within a search directory an exact
  filename now wins, and several files sharing a stem (`boot` matching
  `boot.j2`, `boot.sh`, `boot.j2.bak`) resolve lowest-name-first instead of in
  whatever order the filesystem listed them — a leftover `.bak`/`.orig`/
  `.rpmnew` copy could previously be rendered instead of the real template.
- Templates are read as UTF-8 rather than the machine's locale encoding, so the
  same template tree renders identically on every host (on Windows a non-ASCII
  template could raise `UnicodeDecodeError` or decode wrongly).
- An image with no `template_path` renders instead of raising `AttributeError`
  from `PixieContext.searchpaths`; it now defaults to `[]` as targets already
  did.
- A target whose hostname does not resolve no longer hangs the process.
  `PixieTarget` construction retried the same DNS lookup in an endless loop
  while the name stayed unresolved; because every target is built at startup,
  one not-yet-in-DNS entry made every `pixie` command hang and flood the
  resolver. Resolution is now a single lookup: it fills `ip` on success, and on
  failure logs a warning and leaves `ip` unset. A malformed hostname is warned
  about instead of aborting construction.

### Changed
- `Pixie.lookup_target` matches exactly before it matches by prefix. It
  previously returned the first target whose hostname *started with* the query,
  so with targets `node10` and `node1` in that order, `pixie initiate node1`
  acted on `node10`. Exact id, hostname, MAC and IP matches across the whole
  table now win; a hostname prefix is the fallback, a query matching more than
  one target raises `LookupError` (the CLI reports it and exits 1) instead of
  choosing one, and an empty query matches nothing rather than the first entry.
  MAC queries are parsed, so `AA-BB-CC-00-00-01` and `aabb.cc00.0001` now match
  a target keyed `aa:bb:cc:00:00:01`; previously only the colon spelling did.
- `yaconfiglib` moves to the 0.12 series (`>=0.12.0,<0.13`). The APIs netboot
  uses are unchanged; the previous `<0.12` ceiling made netboot uninstallable
  alongside yaconfiglib 0.12.
- License metadata is PEP 639: `license = "MIT"` plus `license-files`, and the
  legacy `License :: OSI Approved :: MIT License` classifier is gone (building
  now needs `hatchling>=1.27`). The wheel carries the licence at
  `netboot-<version>.dist-info/licenses/LICENSE`.
- Packaging excludes `*.local.*` from both sdist and wheel. A personal override
  such as `pixie.local.yaml` or `AGENTS.local.md` previously shipped, because a
  dotfile pattern does not match a name that has no leading dot.
- `netboot.utils` no longer re-exports `argparse.Namespace` as
  `netboot.utils.Namespace`, where it read as netboot's own config base. The
  config base is `netboot.utils.config.Namespace`, as documented.
- `PixieContext.templates` is gone. It was an annotation only: nothing ever set
  it, so reading it raised `AttributeError`. Search paths are
  `PixieContext.searchpaths`.
- `jinja2` is now `>=3.0,<4`, previously unpinned. jinja2 2.x imports
  `markupsafe.soft_unicode`, removed in MarkupSafe 2.1, so an unconstrained
  resolve could install a pair that raises `ImportError` on `import jinja2`.

### Documentation
- The shipped API header (`src/netboot/AGENTS.md`) is corrected where it did not
  match the code: `hook`'s `value` is positional-only and every hook must return
  a value; `Path` in a Jinja template is `pathlib_next.Path`, not the stdlib's;
  `netboot.netutils` is an attribute, not an importable module path;
  `PixieContext.resources` starts empty and is a hook's slot to fill; template
  search tries each candidate *name* across all search paths (not each path
  across all names); importing `netboot.logging` does import urllib3 and
  disables its insecure-request warning process-wide.
- `docs/cli.md` no longer says `initiate` renders artifacts or that `--iscsi`
  prepares an iSCSI LUN — both are hook extension points; `--help` said the same
  and was corrected too. The `--config` row explains that a `.cfg`/`.ini` suffix
  is parsed as INI, not YAML.
- `docs/extending.md` documents the hook contract: the positional signature, the
  `dict` fourth argument, `netboot=None` for `NewPixieObject`, the prefixed
  `PixieEvent` string values, and that a hook must return the value.
- `docs/configuration.md` example is runnable as written (the MAC-keyed target
  names its zone) and says that a `dnsmasq://` backend has to be provided and
  imported. The shell-template section documents that only `%{NAME}` substitutes.
- The API Reference page covers the content, template, utility and CLI modules
  as well as the engine, and renders members that have no docstring; the site
  gains a Changelog page.
- README/docs install instructions name `netboot[config]`, which the `pixie`
  CLI needs to read a config file, and the README's LICENSE links are absolute
  so they resolve on PyPI.
- `examples/` holds a complete runnable setup — config, both template engines,
  and a `DhcpServer` plugin that prints instead of arming real DHCP — and a
  repo-root `AGENTS.md` covers layout, environments, commands, CI and releasing
  for contributors. The README gains Development and Releasing sections.
- `benchmarks/` documents its metrics and schema and keeps results in the
  tracked `benchmarks/results/`, written by a new `--save` flag, so a
  before/after comparison survives in history. The metric that claimed to
  measure a worst-case target scan actually measured an indexed hit; it is now
  a pair, `lookup_target_by_id` and `lookup_target_scan_by_ip`.
- CI: the release workflow runs a non-deploying docs gate and dispatches the
  docs workflow for the tag (a release created with `GITHUB_TOKEN` starts no
  workflow run, so `release: published` alone never deployed), publishing uses
  `skip-existing`, the test workflow drops to read-only permissions and also
  runs on Windows and macOS, and docs redeploy when `src/`, `README.md` or
  `CHANGELOG.md` change.

## [0.1.3] - 2026-08-16

Dependency floor raise only — no library or CLI behaviour changes.

### Changed
- The four internal dependencies are now pinned to the minor series they are
  supported on, rather than floored at whatever patch happened to be current:
  `duho>=0.5.0,<0.6`, `netimps>=0.2.1,<0.3`, `pathlib_next[uri]>=0.9.0,<0.10`
  and `yaconfiglib>=0.11.1,<0.12`. Each floor is the minor that netboot
  actually requires, and each ceiling stops the next pre-1.0 minor — where, by
  these projects' own versioning rule, the documented API is allowed to break
  — from being resolved unattended. `jinja2` is third-party and stays
  unpinned.
- `duho` moves from `>=0.4.1` to the 0.5 series. 0.5.0 changed option parsing
  for `list`/`set`/`tuple` fields to one value per flag occurrence, which is
  the shape `--cmdspath` (`Arg[list[str], Extend(os.pathsep)]`) is now
  written against. The `CMDS_PATH` layering that `_discover` leans on after
  0.1.2 stopped re-resolving `PIXIE_CMDS_PATH` itself predates this at 0.4.1,
  so it needs no floor above `0.5.0`.
- `netimps` moves from `>=0.2.1` to `>=0.2.1,<0.3` — the floor stays above the
  `.0` on purpose. `Host.try_ip` calls `resolve()` with no `rdtype`, and
  auto-selection of that argument (`"ptr"` for an address literal, `"a"`
  otherwise) is what 0.2.1 added; on 0.2.0 the same call resolves differently.
- `pathlib_next[uri]` moves from `>=0.8.2` to the 0.9 series.
  `Repository.service()` constructs `pathlib_next.uri.Source` directly from
  `str(target.try_ip())`, and that direct-construction path raised
  `socket.gaierror` out of `Source.is_local()` for a bare IPv6-literal host
  until 0.9.0 — the same release that stopped `Source` rendering its password
  in `str()`/`repr()`, so a service URI carrying credentials no longer leaks
  through a traceback frame. 0.9.0's one breaking change is to `PathSyncer`,
  which netboot does not use.
- `yaconfiglib` moves from an unbounded `>=0.10.0` — which spanned two minor
  series — to `>=0.11.1,<0.12`. This floor is also above the `.0` on purpose:
  `load_config` builds `ConfigLoader(..., recursive=True)`, and that setting
  was never forwarded to glob expansion until 0.11.1, so on 0.10.x and 0.11.0
  an argument netboot passes does nothing. 0.11.1 also made `typed_merge`
  read parametrized generics, which is what `Repository.services`
  (`dict[str, UriPath]`) is annotated with.

## [0.1.2] - 2026-08-16

### Added
- Test coverage for behaviour that was documented but unasserted: image
  best-match ordering and its `{}` fallback, DHCP-zone lookup by IP containment
  and its write-back onto the target, the hook chain (value threading, import
  strings, target substitution) and the `initialize`/`complete` event sequence,
  and repository/resource URI assembly with `Host.try_ip`. The suite goes from
  27 tests to 71; no production behaviour changed.
- Python 3.14 is advertised (trove classifier) and tested: the push matrix runs
  3.9–3.14 and the release matrix's ceiling moves from 3.13 to 3.14.

### Changed
- The `duho` floor is now `>=0.4.1`, the release that made `Env` consult
  `os.environ` before a `pixie_env` companion module and turned `CMDS_PATH`
  discovery into a layer merged on top of an explicit `commands=` list. netboot
  now depends on both behaviours.
- `PIXIE_CMDS_PATH` is left to duho instead of being re-resolved by netboot,
  which had discovered the same modules a second time. One consequence is
  visible: on a name clash `PIXIE_CMDS_PATH` now wins over `--cmdspath`, where
  before the option won.
- `black` is the project formatter: it joins the `dev` extra with
  `[tool.black] target-version = ["py39"]` (the supported floor), and the tree
  has been reformatted once to match.
- The `netimps` floor is now `>=0.2.1`. `>=0.0.1` predated the API netboot
  actually calls: the optional-`dnspython` `resolve()` fallback chain (0.2.0),
  its auto-selected `rdtype` (0.2.1), and the 0.2.1 `ping(src=...)` `NameError`
  fix.

### Documentation
- `docs/cli.md` no longer tells operators that `pixie_env` module defaults
  outrank real `PIXIE_*` environment variables. duho 0.4.1 fixed that
  inversion; the note now states the true order (explicit `env` values > real
  `PIXIE_*` > module defaults).
- The shipped API header (`src/netboot/AGENTS.md`) no longer tells consumers the
  `dns` extra is a no-op with `dnspython` arriving transitively — false since
  netimps 0.2.0 made `dnspython` optional. `netboot[dns]` is the only thing that
  installs the dnspython backend; without it `resolve()` falls back to netimps'
  `system`/`nslookup` backends. `resolve`'s documented default `rdtype` is now
  `None` (auto-selects `"ptr"` for address literals, `"a"` otherwise).
- README and the docs landing page no longer claim the IP/MAC/DNS helpers are
  vendored in-tree as `netboot._netutils` — that module was deleted when
  `netimps` was adopted; they now credit `netimps` and name the
  `netboot.utils.net` re-export.
- The `netboot[dns]` extra row describes what it actually does since netimps
  0.2.0: it installs the `dnspython` resolver backend, and hostname targets
  still resolve without it via the system/`nslookup` fallbacks.
- Fixed the `pathlib_next` repository link (`pathlib-next`, with a hyphen) and
  dropped the "once published" install note left on the docs landing page.

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

## [0.1.0] - 2026-07-21

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

[Unreleased]: https://github.com/jose-pr/netboot/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/jose-pr/netboot/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jose-pr/netboot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jose-pr/netboot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jose-pr/netboot/releases/tag/v0.1.0
