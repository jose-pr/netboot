# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-09-17

netboot ships DHCP backends. Additive: nothing documented changed behaviour,
and a config that named no `dhcpservers` scheme netboot implements works as
before.

### Added
- `netboot.dhcp` is a package, and DHCP backends carry **client options**. A
  `dhcpservers` entry is still a plain URI string; its query string now holds the
  options the server should give the client (`?router=10.0.0.1`), alongside each
  backend's own connection settings. `netboot.dhcp.options` models them:
  `GENERIC_OPTIONS` for the names netboot translates, `option-<n>` for anything
  else, `raw.<backend>=` for untranslated backend-native text, and
  `options_builder=my.mod.fn` plus the new `PixieEvent.BuildDhcpOptions` for
  building them from the context. Merge order, later winning: zone defaults, the
  connection query, `image.dhcp_options`, `target.dhcp_options`, the callback,
  the hook.
- **A dnsmasq backend** (`dnsmasq://`), the first netboot ships. It writes
  `dhcp-host`/`dhcp-option`/`dhcp-boot` entries through `pathlib_next` paths, so
  the same configuration serves a local dnsmasq or one reached over `sftp://`
  (`netboot[ssh]`). `hostsfile`/`optsfile` may be a **directory** (one file per
  target — preferred, and with `--dhcp-hostsdir`/`--dhcp-optsdir` dnsmasq re-reads
  them with no signal at all) or a **file**, where netboot edits only its own
  marked region and never touches a line it did not write. `reload=` runs a
  command, over ssh when the URI names a host; omitting it is fine for a watched
  directory and an error for a file, which dnsmasq never re-reads by itself.
- **A Kea backend** (`kea://`, `keas://` for https). It sends `reservation-add`
  and `reservation-del` to the control agent, mapping `boot-file-name`,
  `next-server` and `host-name` onto reservation *fields* and everything else
  onto `option-data`. The `subnet-id` comes from the zone (`subnet_id:`) or is
  discovered once by matching the zone's network against `config-get`; when
  neither works netboot refuses rather than guessing, because a reservation in
  the wrong subnet silently never matches. A `result: 2` from Kea is reported
  with the likely cause (host_cmds not loaded, or a read-only hosts backend),
  and an existing reservation for the same MAC is refused rather than
  overwritten. Needs `netboot[kea]`.
- **An ISC dhcpd backend** (`dhcpd://`) over OMAPI, the only way to give dhcpd a
  reservation without rewriting and reloading its config. Options are rendered
  into the host's `statements`, which is dhcpd config *source*: text values are
  quoted with `"` and `\` escaped, a value meant to be an address must look like
  one, and a newline is refused outright — a value cannot end its statement and
  start another. The OMAPI secret comes from `keyfile=` or
  `$PIXIE_DHCPD_OMAPI_KEY`, never from the URI. Needs `netboot[dhcpd]`. Note
  that a host added over OMAPI does not survive a dhcpd restart by itself: that
  is dhcpd's design, not netboot forgetting it.
- **A Windows DHCP Server backend** (`windhcp://`), driving the `DhcpServer`
  PowerShell module over **ssh** (the default, no dependency — it uses the system
  `ssh` client, so your config, keys, agent and jump hosts apply) or **WinRM**
  (`netboot[winrm]`). The transport host and the DHCP server are separate:
  `server=` becomes `-ComputerName` when the cmdlets should act elsewhere. The
  scope is the zone's network address, resolved when the target is applied and
  overridable per zone. No value is interpolated into the script: parameters
  travel as a JSON payload PowerShell parses, so a target name or option value
  cannot become a statement.
- New extras, each carrying exactly one backend's dependency: `netboot[kea]`,
  `netboot[dhcpd]`, `netboot[winrm]`, and `netboot[ssh]` for dnsmasq over
  `sftp://`. A local dnsmasq and `windhcp://` over ssh need none. Verified on a
  clean wheel install: importing `netboot.dhcp` loads no backend dependency,
  those two backends construct, and the other two raise `ImportError` naming
  their extra.
- Anything that varies per target is refused in a connection string and resolved
  when the target is applied instead: `subnet_id`/`scope` belong to the zone,
  `boot-file-name`/`next-server`/`tftp-server-name` to the image or target. The
  error names where each belongs. `from netboot.dhcp import DhcpServer, DhcpZone`
  is unchanged, and a backend module is imported only when a config names its
  scheme.

## [0.2.0] - 2026-09-17

A correctness and hardening release from a full review of the code base.
Several documented behaviours changed, so read **Changed** before upgrading:
target lookup, `shell_quote`, shell-template placeholders, relative
`template_path` resolution and undefined template variables all behave
differently on purpose.

### Added
- `PixieError`, `PixieLookupError` and `PixieConfigError`. Netboot's deliberate
  failures are now distinguishable from bugs: lookups that fail or are
  ambiguous raise `PixieLookupError` (still a `LookupError`) instead of a plain
  `Exception`, and the messages name the target and what was configured.
- `netboot.logging.quiet_noisy_dependencies(insecure_warnings=False)`.
- `netboot[http]` extra. `http`/`https` repository services reach the network
  through `requests`, which nothing installed: `pathlib_next[uri]` does not
  depend on it, so `Repository.service("http")` failed with
  `ModuleNotFoundError: No module named 'requests'` on any install that did not
  happen to have it. Install `netboot[http]` for http-served repos; `file` and
  `tftp` repos, and rendering, need nothing extra. Without the extra those calls
  now raise `ImportError` naming it instead of failing inside the library.

### Changed
- **A template variable with no value now raises by default.** The two engines
  disagreed: the shell engine raised while Jinja rendered an empty string, so a
  typo in a `.j2` file shipped a boot artifact with a blank where a kernel path
  belonged, and the behaviour depended on the file's suffix. The new top-level
  config key `templates_undefined` decides, and means the same in both engines:
  `strict` (default) raises naming the variable, `lenient` renders an empty
  string with a warning — the old Jinja behaviour — and `debug` leaves the
  placeholder in the output. Set `templates_undefined: lenient` to keep
  templates that relied on blanks working.

### Security
- Config loading is hardened: the loader runs with `allow_commands=False` and
  `sandbox=True`, so `{{ ... }}` interpolation in a config value renders in
  jinja2's sandbox and a config document can no longer run commands. Previously
  a value such as `{{ ''.__class__.__mro__[1].__subclasses__() }}` was evaluated
  unsandboxed — and a netboot config is often assembled by `!include` from
  inventory exports rather than written by hand. Ordinary interpolation is
  unchanged; set `YACONFIGLIB_CONFINE_TO` to also restrict where `!include` may
  read from.
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
- A `StartPixieInit` hook's return value is what gets built. The result was
  stored but the original config was read, so a hook written in the usual
  non-mutating style (`return {**value, "targets": ...}`) had its targets
  ignored while its `templates` took effect — the config ended up half applied.
  A hook that returns `None` for `StartPixieInit` or `NewPixieObject` now raises
  `PixieConfigError` naming the contract instead of failing obscurely later.
- A template name containing `..` is refused. Names are resolved against every
  search root, so `../../etc/passwd` reached outside them.
- Arming DHCP is all or nothing. `pxe_init` stopped at the first backend that
  raised, leaving the target armed on the earlier ones — it could boot an
  installer from one server while another handed out its normal lease. The
  already-armed backends are now rolled back before the error propagates.
  `pxe_complete` takes the opposite approach: every backend is disarmed even if
  one fails, and the first error is raised afterwards, instead of leaving the
  rest armed.
- `Pixie.complete` no longer needs the target's image to exist. Retiring an
  image from the config used to make the machines that used it impossible to
  clean up, because building the context failed before DHCP could be disarmed.
- A global named `target`, `image`, `dhcpzone`, `repos` or `resources` no longer
  replaces the render context's own field of that name; it is dropped with a
  warning.
- A zone is chosen by longest prefix, not declaration order: with `10.0.0.0/16`
  and `10.0.0.5/24` both configured, a host in the /24 now gets the /24.
- A `None` config entry (`targets: {host1:}` — valid YAML meaning "all
  defaults") no longer crashes with `AttributeError`, and an entry that is
  neither a mapping nor an object raises `PixieConfigError` naming it.
- `lookup_image` treats any falsy `match` result as "no match": a `match`
  override returning `None` used to crash the sort.
- `DhcpZone` no longer normalises the caller's lists in place (they may be
  shared through a config merge), drops an unparseable nameserver with a warning
  instead of turning it into `None`, and `get_local_server` accepts the strings
  config actually holds rather than raising on them. Two plugins claiming one
  URI scheme now log a warning naming both.
- A `Pixie` subclass's annotated attribute keeps its class default when the
  config does not mention it (it was overwritten with `None`), and an
  `Optional[...]` annotation no longer crashes construction — its origin is not
  a constructor, and on 3.9 it is not even a class.
- The `_id` retry when building a config object only catches "this class takes
  no `_id`"; any other `TypeError` from the value class propagates instead of
  being hidden behind a second construction attempt.
- `Pixie.globals` is really the deep copy the docs promised. The copy made in
  `__init__` was immediately overwritten by the annotated-attribute loop, so
  nested values stayed shared with the caller's config dict (mutating
  `engine.globals["a"]["b"]` changed the caller's mapping) and any global whose
  key started with `_` was silently dropped. Both are fixed: `_`-prefixed
  globals are ordinary variable names and are kept.
- `Pixie.lookup_dhcpzone` returns `None` instead of raising `AttributeError`
  when the target has no usable IP — the documented MAC-keyed target shape hit
  this on every `initiate` that did not name a `dhcpzone`.
- Repository URLs are built correctly from an `address` that carries a port:
  `mirror.example:8080` became a hostname with an escaped colon
  (`mirror.example%3A8080`) instead of a host and a port. `[2001:db8::1]:8080`
  works too, and a repo with no address warns instead of silently producing a
  hostless `http:/path`.
- An `https` service keeps the configured name rather than substituting the
  resolved IP, which broke certificate validation and name-based virtual hosts.
  Other schemes still resolve, since a PXE client often has no DNS yet.
- `repo / "/sub"` extends the repository root instead of replacing it, and a
  trailing slash no longer doubles up.
- A command module whose body is `main(netboot, args, conf)` gets netboot's
  contract. Dispatch looked only at `module.run`, while duho resolves
  `main` → `run` → `call`, so such a command was handed to duho's
  single-argument dispatch and failed on the signature.
- Two context values that flatten to the same shell-template variable (a global
  `target_ip` beside `target.ip`, or `domain` beside `DOMAIN`) now log a warning
  naming both; the later one still wins, but silently building an artifact from
  the wrong value is what this prevents.
- A relative `template_path` is resolved inside the template roots
  (`config["templates"]`, `./templates` by default) rather than against the
  process's working directory, so a config means the same thing whichever
  directory `pixie` runs from. An image's or target's `template_path` still
  *extends* the roots, which are searched last; absolute paths and URIs are used
  as given. A config that wrote `template_path: [templates/debian]` to reach
  `./templates/debian` should now write `[debian]`.
- Rendered artifacts keep the template's final newline. The Jinja engine dropped
  it while the shell engine kept it, so the same content rendered differently
  depending on the file's suffix, and a kickstart or iPXE script could end
  without a newline.
- MAC-less targets no longer all look for `00-00-00-00-00-00.<name>` first: the
  null MAC is skipped when building candidate template names, as the unset IP
  already was. One stray file of that name used to apply to every such target.
- A `http://...` entry in `templates` or in an image's `template_path` stays a
  URI instead of being turned into a directory named `http:` under the working
  directory, so URI search paths are searched at all. A repository's `local`
  path accepts a Windows drive path (`C:\\tftp`), which was previously read as
  URI scheme `c` and then failed on every read.
- An edited shell template is picked up again: the loader assigned its freshness
  *check function* to `is_up_to_date`, and a function object is always truthy,
  so a cached template was never reloaded.
- `Loader` works without a `PixieContext` in the environment: it resolves a
  plain template name instead of raising `AttributeError`.
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
- `pathlib_next[uri]` floor raised to `>=0.9.9`. Measured: recursive
  `!include` globs (`sub/**/*.yaml`) fail on pathlib_next 0.9.5+ unless
  yaconfiglib is 0.12.0+, and yaconfiglib 0.12.0 itself requires
  pathlib-next>=0.9.9 — so the old `>=0.9.0` claim described a combination that
  cannot work. `tests/test_config_discovery.py` now fails below the floor
  instead of passing quietly.
- `yaconfiglib` moves to the 0.12 series (`>=0.12.0,<0.13`). The APIs netboot
  uses are unchanged; the previous `<0.12` ceiling made netboot uninstallable
  alongside yaconfiglib 0.12.
- The package logger is named `netboot`, not `NETBOOT`, and `-v`/`-q` now reach
  it: duho sets the level of the logger named after the parser, which never
  matched the one netboot writes to, so the verbosity flags had no effect on
  netboot's own output. Anything filtering on the old name must use `netboot`.
- Importing `netboot` no longer has logging side effects. It used to quiet
  urllib3/paramiko and disable urllib3's `InsecureRequestWarning`
  process-wide — a decision belonging to the application, not to a library, and
  it imported urllib3 as a side effect of `import netboot`. The `pixie` CLI
  calls `quiet_noisy_dependencies()`; embedders opt in.
- The CLI reports operator errors as one line and exits 2 instead of printing a
  traceback: a missing or malformed config, a non-mapping config, an unknown
  image or zone, or a sandbox refusal. `-v` still shows the traceback, and an
  unexpected exception is still raised in full. `--version` and usage errors
  say `pixie` rather than the class name `Pixie_`.
- An absolute Windows path is accepted for `--config`/`--baseconfig`/template
  paths. `C:\\srv\\tftp` was parsed as URI scheme `c`, which then failed with
  `NotImplementedError`; a single-letter scheme is now read as a drive letter.
- An empty or comment-only config loads as `{}` rather than raising
  `AttributeError`, a scalar `templates:` is accepted as a one-element list, and
  a command returning a non-int value is warned about and treated as success
  rather than crashing after the command's work is done.
- License metadata is PEP 639: `license = "MIT"` plus `license-files`, and the
  legacy `License :: OSI Approved :: MIT License` classifier is gone (building
  now needs `hatchling>=1.27`). The wheel carries the licence at
  `netboot-<version>.dist-info/licenses/LICENSE`.
- Packaging excludes `*.local.*` from both sdist and wheel. A personal override
  such as `pixie.local.yaml` or `AGENTS.local.md` previously shipped, because a
  dotfile pattern does not match a name that has no leading dot.
- The engine moved to `netboot.engine`, leaving the package root as a surface
  (44 lines). `from netboot import Pixie` and every other documented import are
  unchanged; `netboot.engine.Pixie` is the same object. `netboot._version`
  holds the version lookup.
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
- The public API is documented in the source, so the API Reference renders
  prose rather than bare signatures: 41 docstrings added, leaving only dunders
  undocumented (which the reference filters out anyway).
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

[Unreleased]: https://github.com/jose-pr/netboot/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/jose-pr/netboot/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jose-pr/netboot/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/jose-pr/netboot/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jose-pr/netboot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jose-pr/netboot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jose-pr/netboot/releases/tag/v0.1.0
