# `netboot` — public API header

Header-file-style reference for the `netboot` package: every public export with
its signature, arguments, contract, and gotchas, so this module can be
consumed without reading its source. Kept current with the public API. For the
project overview, install instructions and CLI usage, see the shipped
`README.md`, or <https://github.com/jose-pr/netboot>.

## Errors (`netboot`)

- **`PixieError(Exception)`** — base for what netboot raises on purpose, as
  opposed to a bug. The CLI reports these as one line and exits 2.
  - **`PixieLookupError(PixieError, LookupError)`** — a target, image or zone
    could not be resolved, or a target query was ambiguous.
  - **`PixieConfigError(PixieError, ValueError)`** — the configuration cannot
    be used as given (for example a top level that is not a mapping).

## Engine (`netboot.engine`, re-exported from `netboot`)

The engine lives in `netboot.engine`; the package root re-exports it, so
`from netboot import Pixie` is the supported import and `netboot.engine.Pixie`
is the same object.

- **`Pixie(hooks=(), **config)`** — the engine. `config` is the merged config
  mapping: `targets`, `images`, `dhcpzones`, `repos` (each a `dict[id, ...]`
  built into the corresponding class via its type hints — `TypeError` from the
  value class falls back to a no-`_id` constructor call; a `None` entry
  (`targets: {host1:}` in YAML) means "all defaults", and an entry that is
  neither a mapping nor an object raises `PixieConfigError`), `globals` (dict;
  deep-copied at construction, so later edits to the caller's mapping do not
  reach the engine), `defaults` (per-collection default mappings), plus any other
  annotated `Pixie` attribute. `hooks` is a sequence of callables or
  `"module.func"` import-path strings; resolved once in `__new__`. Every
  config-driven step fires a `PixieEvent` through the hook chain (see below)
  so hooks can intercept object construction and property values before
  they're set.
  - **`.targets`** / **`.dhcpzones`** / **`.images`** / **`.repos`** —
    `dict[str, ...]` of `PixieTarget` / `DhcpZone` / `PixieImage` /
    `Repository`, keyed by config id.
  - **`.globals`** — `dict`, layered into every render context.
  - **`.templates_undefined`** — `"strict"` (default), `"lenient"` or
    `"debug"`, from the config key of the same name: what a template variable
    with no value does. `strict` raises naming it, `lenient` renders an empty
    string with a warning, `debug` leaves the placeholder in the output. It
    means the same in both engines; an unknown value raises
    `PixieConfigError` when a context is built.
  - **`.hook(event, value=None, /, **kwargs) -> value`** — run the hook chain
    for `event`, threading `value` through each `f(event, netboot, value,
    kwargs)` and returning the (possibly transformed) result. `value` is
    **positional-only**: `hook(event, value=x)` does not set it, it passes
    `x` as a `kwargs` entry named `value` and threads `None` instead. Each
    hook receives `kwargs` as a plain `dict` (a fourth positional argument,
    not `**`-expanded) and **must return** the value to pass on — a hook that
    returns nothing replaces it with `None` for every later hook and for the
    caller.
  - **`.lookup_target(target: str) -> PixieTarget | None`** — exact id match
    first, then an exact match on hostname (case-insensitive), MAC or IP
    across the whole table, and only then a hostname *prefix* match. MAC input
    is parsed, so colon, hyphen and Cisco-dot spellings all match, and the
    null MAC matches nothing. Raises `PixieLookupError` (a `LookupError`)
    when a query matches more than one target rather than picking one; an
    empty string matches nothing.
    Fires `PixieEvent.LookupTarget` (may substitute a `PixieTarget` directly)
    then `PixieEvent.FoundTarget`; `None` if nothing resolves to a
    `PixieTarget`.
  - **`.lookup_image(name: str, target=None) -> PixieImage`** — the image
    whose `.match(img_name, name)` returns the highest truthy value (default
    `PixieImage.match` is exact-name equality); falls back to an empty dict
    if nothing matches (**not** `None` — check truthiness carefully). Fires
    `PixieEvent.FoundTargetImage`.
  - **`.lookup_dhcpzone(name: str, target=None) -> DhcpZone | None`** — by id;
    if `name` is empty and `target` is given, uses `target.dhcpzone`, else the
    **most specific** zone whose `.network` contains `target.ip` (longest
    prefix, so declaration order does not decide it) and caches that id back
    onto `target.dhcpzone`. A target with no usable IP yields `None` rather
    than raising. Fires `PixieEvent.FoundTargetDhcpzone`.
  - **`.make_context(target, globals: list[dict] = None, require_image=True)
    -> PixieContext`** — resolves image + dhcp zone for `target`, merges
    `[self.globals, *globals, image.globals, dhcpzone.globals, target.globals]`
    (image/dhcpzone/target objects are shared across targets and never
    mutated) into a fresh `PixieContext`, attaches a `tplx.Loader` over
    `config["templates"]`, and sets `.version`. Globals named `target`,
    `image`, `dhcpzone`, `repos` or `resources` are **dropped with a warning**:
    they would replace the context's own fields. Raises `PixieLookupError`
    naming the target and what was configured if the image or dhcp zone cannot
    be resolved; `require_image=False` (what `.complete` uses) substitutes an
    empty `PixieImage` instead, so a machine can still be cleaned up after its
    image is retired from the config. Fires `PixieEvent.PixieContextForTarget`.
  - **`.initialize(target) -> PixieContext`** — `make_context` then
    `ctx.pxe_init(self)` (arms every `dhcpzone.dhcpservers` for the target).
    Fires `StartPixieInitialize` / `EndPixieInitialize`.
  - **`.complete(target) -> PixieContext`** — `make_context` then
    `ctx.pxe_complete(self)` (disarms every `dhcpzone.dhcpservers`). Fires
    `StartPixieComplete` / `EndPixieComplete`.
  - **`.VERSION`** — class attr, `netboot.__version__` at class-definition time.

- **`PixieEvent`** (`StrEnum`) — hook event names: `NewPixieObject`,
  `StartPixieInit`, `SetPixieProperty`, `PixieInitiated`, `LookupTarget`,
  `FoundTarget`, `FoundTargetImage`, `FoundTargetDhcpzone`,
  `PixieContextForTarget`, `BuildDhcpOptions`, `StartPixieInitialize`,
  `EndPixieInitialize`, `StartPixieComplete`, `EndPixieComplete`.

- **`PixieTarget(**kwargs)`** (`argparse.Namespace` + `yaconfiglib.OpaqueMerge`)
  — `_id`, `hostname`, `ip` (`IPAddress`), `mac` (`MACAddress`), `image`,
  `dhcpzone`, `globals` (`dict`), `template_path` (`list[str | Path]`).
  Construction fills gaps: a MAC-shaped `_id` with no explicit `mac` is
  adopted as the MAC (else `mac` defaults to the null MAC
  `00:00:00:00:00:00`); an `_id` that looks like an IP fills `ip`, otherwise a
  non-MAC `_id` fills `hostname`; then, if `ip` is still unset and a hostname
  is known, **exactly one** forward lookup
  (`netimps.resolve`, no reverse lookup) fills it. A name that does
  not resolve — including when no resolver is reachable at all — leaves `ip`
  unset and logs a warning; it is never retried, and never raises. `hostname`
  is lower-cased. The `dns` extra is optional: without it netimps still
  resolves through its system/`nslookup` backends.

- **`PixieImage(**kwargs)`** (`content.Resource`) — `template_path`,
  `globals`, plus `Resource`'s `src`/`path`, which address the image's
  **artifacts** in a repository (kernel, initrd) and play no part in template
  lookup. **`.match(name: str, check: str)`** — override point for custom
  image-selection logic; default is `name == check`. Returning a comparable
  (e.g. `int`) instead of a bare bool lets `Pixie.lookup_image` prefer the
  best of several matches.

- **`PixieContext(**kwargs)`** (`argparse.Namespace`) — built by
  `Pixie.make_context`, not constructed directly. Fields: `target`, `image`,
  `dhcpzone`, `repos` (`dict[str, Repository]`), `resources`
  (`dict[str, Resource]`), `generated` (`datetime`, set at construction),
  `version` (`str`), `_loader` (a `tplx.Loader`). **`resources` starts
  empty**: there is no `resources:` config key, so it is a slot for a hook (or
  a caller) to fill before rendering — `.resource()` returns `None` until
  something does. Template search paths are `.searchpaths` (target then image
  `template_path`), not a `templates` field.
  - **`.render(filename: str, strict=True) -> str | bytes | None`** — render
    the template found for `filename` (see "Templates" below) against this
    context; `bytes` for a file the copy engine claims. `strict=True`
    (default) re-raises render errors; `strict=False` logs at DEBUG and
    returns `None`.
  - **`.resource(name: str | Resource, service: str = None) -> UriPath | None`**
    — resolve a resource (by id, looked up in `.resources`, or a `Resource`
    directly) to a fetchable URI via its repo's `service`. `None` if the repo
    or resource can't be found.
  - **`.resource_repo(name: str) -> Repository | None`** — the `Repository`
    backing resource `name`.
  - **`.searchpaths -> list[Path]`** — `target.template_path +
    image.template_path`. These **extend** the engine-wide template roots
    (`config["templates"]`, `./templates` by default) and are consulted before
    them. A *relative* entry is resolved inside each root
    (`template_path: [debian]` -> `<root>/debian`), so it does not depend on the
    process's working directory; an absolute path or a URI is used as given.
  - **`.pxe_init(netboot) -> Self`** / **`.pxe_complete(netboot) -> Self`** —
    arm/disarm every `dhcpzone.dhcpservers` for this context; called by
    `Pixie.initialize`/`.complete`, not usually invoked directly. Both try
    every backend. `pxe_init` logs a backend that raises as a **warning** and
    carries on; it raises (the first error) only when **no** backend could be
    armed, and nothing is rolled back, so a target may end up armed on some
    servers and not others. `pxe_complete` never raises for a backend: each
    failure is logged as a warning and the rest are still disarmed.

## DHCP (`netboot.dhcp`)

- **`DhcpServer(uri: str)`** — base class for DHCP backends, dispatched by
  URI scheme: `DhcpServer("dnsmasq://...")` returns an instance of whichever
  registered subclass has a matching lowercased class name (any subclass
  depth, so a plugin may subclass an intermediate base). Raises `ValueError`
  if no subclass matches the scheme — import the plugin module first (e.g.
  via CLI `--load-module`). `.uri` holds the original URI.
  - **`.add_target(ctx: PixieContext)`** / **`.remove_target(ctx)`** — no-ops
    on the base class; a backend overrides these to actually arm/disarm.
  When two registered subclasses claim the same scheme, the most recently
  defined one wins and a warning names both — silently picking one makes the
  other plugin look broken. **`.get_local_server(servers, default)`** accepts
  the strings config and globals actually hold as well as parsed addresses,
  skips anything unparseable with a warning, and returns `default` when no
  server lies inside the zone's network.

- **`DhcpServer.SETTINGS`** — the query keys a backend reads as *connection*
  settings. Every other key in a `dhcpservers` URI query is a **client option**
  (see `netboot.dhcp.options`), so the two vocabularies must stay disjoint.
- **`DhcpServer.options_for(ctx) -> DhcpOptions`** — the merged client options
  for this target on this server.

- **`DhcpZone(**kwargs)`** (`Namespace` + `OpaqueMerge`) — `network`
  (`IPNetwork`), `gateway` (`IPAddress | None`), `domain` (`str | None`),
  `search` (`list[str]`), `nameservers` (`list[IPAddress]`), `globals`,
  `dhcpservers` (`list[DhcpServer]`, strings coerced via `DhcpServer(uri)`).
  Accepts `gateway`+`netmask`, or a CIDR `network`/`gateway` string, and
  derives the rest. **`.nameserver`** — first of `.nameservers`, or `""`.
  **`.get_local_server(servers, default)`** — first `server` contained in
  `.network`, else `default`.

## DHCP backends (`netboot.dhcp.*`)

Four ship, each imported only when a config names its scheme — so importing
`netboot.dhcp` pulls in none of their dependencies. Every one takes its client
options from the URI query (see below) and translates them.

- **`dnsmasq://[host]/?hostsfile=&optsfile=&reload=&pidfile=`**
  (`netboot.dhcp.dnsmasq`) — writes `dhcp-host`/`dhcp-option`/`dhcp-boot`
  entries. Paths may carry their own scheme; a bare path is local when the URI
  has no host and `sftp://<host>/<path>` when it does (`netboot[ssh]`). A
  **directory** gets one file per target; a **file** gets a marked region netboot
  owns. `reload=` runs a command, over ssh when the URI names a host — omit it
  for a `--dhcp-hostsdir`, which dnsmasq re-reads by itself; a plain file
  without it raises.
- **`kea://`, `keas://`** (`netboot.dhcp.kea`, `netboot[kea]`) —
  `reservation-add`/`reservation-del` through the control agent. `?service=`
  selects `dhcp4`/`dhcp6`. Needs the **host_cmds** hook and a writable hosts
  backend; `result: 2` is reported with that cause. `subnet-id` comes from the
  zone (`subnet_id`) or a matched `config-get`.
- **`dhcpd://host:7911/?keyname=&keyfile=`** (`netboot.dhcp.dhcpd`,
  `netboot[dhcpd]`) — an OMAPI host object whose `statements` carry the options,
  escaped. Secret from `keyfile=` or `$PIXIE_DHCPD_OMAPI_KEY`. A host added this
  way does **not** survive a dhcpd restart by itself.
- **`windhcp://[user@]host/?transport=local|ssh|winrm&method=powershell|netsh&server=&auth=&port=&ssl=`**
  (`netboot.dhcp.windhcp`) — Windows DHCP over **`local`** PowerShell (the default
  when the URI has no host, e.g. `windhcp:///?server=dhcp01`; refused on
  non-Windows), the system `ssh` client (the default for a named host, no
  dependency) or WinRM (`netboot[winrm]`, password from
  `$PIXIE_WINDHCP_PASSWORD`, account from the URI or `$PIXIE_WINDHCP_USER`). All
  three send the **same script**: `local` is the ssh transport without the ssh. **`transport=` is how we get a shell, `method=` is
  what runs there**: `powershell` (default) for the `DhcpServer` module's
  cmdlets, `netsh` for `netsh dhcp server ...` on a host without that module.
  `server=` becomes `-ComputerName`, or netsh's `\\server` (already-`\\`-prefixed
  is left alone). An option's value is always a **list** in the payload, because
  `Set-DhcpServerv4OptionValue -Value` takes `String[]` and refuses a joined
  `"a,b"`; netsh takes one argument per value. Under `method=netsh` the script
  **reads the state back** (`netsh ... dump`) and fails naming any option that is
  missing or short of values, because netsh exits 0 and claims success after
  silently dropping a value it dislikes. Parameters travel as a JSON payload, never interpolated into
  the script — including under `netsh`, which is invoked as
  `& netsh @($c.Args)` so every argument stays an array element. netsh is judged
  by `$LASTEXITCODE`, not its localised success line; a `delete` carries
  `Ignore` so cleanup can re-run; option data types come from `_OPTION_TYPES`
  (unlisted → `STRING`), a multi-valued option becomes one argument per value
  (the cmdlets take it comma-joined instead), and a reservation is added as
  `BOTH`. The scope is the zone's network address, overridable with `scope`
  **on the zone**.

Each raises `PixieLookupError` for a target with no MAC — every one of these
servers identifies a reservation by it.

- **Completion** (`netboot.dhcp.completion`) — `dhcp_complete:` decides what a
  finished target's entry becomes, because one that still gets `boot-file-name`
  reinstalls itself. **`build_completion(ctx) -> DhcpCompletion`** layers zone →
  image → target (most specific wins); `.keep` is `False` by default (remove the
  entry, netboot's original behaviour), and `.apply_to(options)` returns the
  options a kept entry should have — **`BOOT_OPTIONS`** (`boot-file-name`,
  `next-server`, `tftp-server-name`) **replaced** by `.options`, everything else
  untouched. `options` without `keep: true` is a config error, not a no-op.
  The engine calls **`DhcpServer.complete_target(ctx)`**, which is
  `remove_target` unless the entry is kept, in which case it first calls
  `remove_condition_member` for every condition (a kept entry still matching one
  would keep being served the installer) and then **`keep_target(ctx,
  completion)`**. The base `keep_target` raises `NotImplementedError`, so a
  backend that cannot keep an entry is reported rather than silently ignoring the
  config. All four shipped backends implement it; `kea` uses `reservation-add`'s
  upsert so a failure leaves the old reservation rather than none.
- **Conditions** (`netboot.dhcp.conditions`) — `dhcp_when:` is a **mapping keyed
  by name**, and the key is the backend's own construct name.
  **`build_conditions(ctx) -> dict[str, DhcpCondition]`** layers zone → image →
  target by name; **`DhcpCondition`** carries `.name`, `.match`, `.options` and
  `.option_codes`; **`MATCH_KEYS`** is `user-class` (77) and `vendor-class` (60),
  the tests every backend can express.
  The backend contract is a ladder: **`ensure_condition(ctx, condition) -> str`**
  uses the construct if it exists, creates it if it can, raises
  **`ConditionMissing`** (carrying `.recipe`) if it cannot, and raises
  **`ConditionUnsupported`** if the backend has no such concept at all — the base
  implementation. **`condition_recipe(ctx, condition) -> str`** is that recipe,
  and the same text the error carries. **`remove_condition_member(ctx, condition)`**
  detaches a target that *keeps* its reservation, never deleting the shared
  construct. Neither exception is a `PixieError` (the engine imports this
  package), and both are caught by best-effort arming, so one backend refusing
  leaves the others armed.
  Per backend: `windhcp` creates a scope-level policy **plus the user/vendor
  class it references** (Windows refuses a policy naming an undefined class) and
  always does so through the cmdlets, because **netsh has no policy verb at all**.
  **A reservation's options outrank every policy** (reservation > scope policy >
  server policy > scope > server, per option), so an overridden option id is
  **omitted from the reservation** and served by policies: one per condition
  matched on the target's MAC *and* the test, plus a **base** policy on the MAC
  alone carrying what the reservation would have said, with `ProcessingOrder` set
  explicitly so the conditional one is consulted first. `conditions=shared`
  instead makes one policy per condition name with no MAC, for a host where
  netboot cannot create policies (an admin then creates them once); the base value
  is the operator's to provide in that mode. Completion removes a target's own
  policies and never a shared one;
  `dhcpd` inlines an `if` (default) or writes a named group with
  `conditions=group`; `kea` adds a client class with `class-add` when the
  `class_cmds` hook is loaded and names it in the reservation's
  `client-classes`; `dnsmasq` **refuses**, because the tag needs a `dhcp-match`
  line in a config file netboot does not own.
- **`DhcpServer.extras(ctx, phase="add") -> list`** — the extension point for
  anything netboot does not model: an extra statement, an extra command, or a
  condition (the iPXE chainload is the usual one). The default returns the
  configured `raw.<backend>` fragments for that phase; a subclass overrides it to
  decide **per target**, and should call `super()` to keep the config's own. The
  fragment shape is the backend's: dhcpd statements, dnsmasq config lines, a kea
  `option-data` entry, or — for windhcp — a PowerShell line or a
  `{"Args": [...], "Ignore": bool}` netsh command, which is rendered as a quoted
  `netsh` call when the method is `powershell` rather than dropped. `phase` is
  `add` or `remove` (**`PHASES`**); a bare `raw.<backend>` is `add`,
  `raw.<backend>.remove` is teardown. Fragments are emitted **verbatim** — no
  parsing, validation or escaping — and always **after** the modelled options,
  so a conditional overrides them.

- **`pixie dhcp-config [target] [--apply] [--as USER]`**
  (`netboot.cmds.dhcp_config`) — prints each server's `condition_recipe()` with a
  header naming the server and condition; **contacts nothing** without `--apply`.
  `--apply` calls `ensure_condition` per server and returns 1 if any failed,
  having done the rest. `--as` implies `--apply` and prompts with `getpass`, only
  on a TTY — without one it fails naming `PIXIE_WINDHCP_PASSWORD`, rather than
  blocking an unattended run. The password goes into the environment the backends
  already read, never a log or a script. **The only command that prompts**; this
  is where privileged one-offs live so `initiate`/`complete` stay unattended.

## DHCP options (`netboot.dhcp.options`)

- **`GENERIC_OPTIONS`** — the option names netboot models and every backend
  translates (`router`, `domain-name-servers`, `domain-name`, `domain-search`,
  `next-server`, `boot-file-name`, `tftp-server-name`, `host-name`,
  `subnet-mask`, `broadcast-address`, `lease-time`, `ntp-servers`,
  `vendor-class-identifier`). A numeric `option-<n>` or a bare number is always
  accepted, for anything not modelled.
- **`APPLY_TIME`** — names that are resolved **when a target is applied**, never
  from a connection string: `subnet_id`/`scope` belong to the **zone**,
  `boot-file-name`/`next-server`/`tftp-server-name` to the **image or target**,
  `host-name` to the target itself. Passing one in a URI query raises
  `PixieConfigError` naming where it belongs — a boot file pinned to a
  connection would hand every target on that server the same one.
- **`DhcpOptions(dict)`** — the merged options, plus **`.raw`** /
  **`.raw_for(backend)`**: backend-native text from `raw.<backend>=` in the
  query, never translated.
- **`build_options(ctx, server) -> DhcpOptions`** — merges, later winning: zone
  defaults (from `gateway`/`nameservers`/`domain`/`search`/`network`) → the
  server URI's query → `image.dhcp_options` → `target.dhcp_options` → the
  server's `options_builder=` callback (`fn(ctx, options) -> options`) → the
  `PixieEvent.BuildDhcpOptions` hook. An unknown option name raises.
- **`split_query(uri, settings, backend)`** → `(settings, options, builder)`.
  A repeated key becomes a list, which is how a multi-valued option is written.

## Content (`netboot.content`)

- **`Resource(**kwargs)`** — `path` (`Pathname`, coerced via `_parse_path`),
  `src` (repo id string). `resource / "sub"` joins the path, same `src`.
- **`Repository(**kwargs)`** — `address` (`netimps.Host`), `services`
  (`dict[str, UriPath]`), `local` (`UriPath | None`, a filesystem/local
  service; a Windows drive path such as `C:\tftp` is read as a path and becomes
  a `file:` URI, not URI scheme `c`). `repo / "sub"` (`.joinpath`) returns a **new** `Repository` with
  every service (and `.local`) suffixed by `sub` — the original is untouched.
  **`.get(*path, service=None) -> UriPath | None`** — join `path` onto the
  named service's base URI (`service=None` → `.local`, scheme `"file"`);
  `None` if that service isn't defined. **`.service(name) -> UriPath | None`**
  — the base URI for `name`. For a *relative* service path the authority is
  filled in from `.address`: resolved to an IP (`Host.ip()`, or the name as
  written when it does not resolve) for every scheme
  except `https`, which keeps the configured name so TLS validation and
  name-based virtual hosts still work. An `address` of the form `host:port` or
  `[v6]:port` becomes a real host and port (**`netimps.split_host`**; a v6
  literal comes back unbracketed, which is what `pathlib_next`'s `Source` wants,
  and a malformed port raises `PixieConfigError` naming the repo) rather than a
  hostname containing an
  escaped colon, and a repo with no address warns. A service written as a full
  URI keeps its own authority untouched. `repo[path, service]` is sugar for `.get(path, service=service)`.
  An `http`/`https` service needs the **`http` extra** (`pip install
  netboot[http]`): pathlib_next reaches those schemes through `requests`, which
  `pathlib_next[uri]` does not install. Without it `.service()`/`.get()` raise
  `ImportError` naming the extra; `file`/`tftp` services and rendering need
  nothing extra.

## Templates (`tplx`)

Rendering is done by [`tplx`](https://pypi.org/project/tplx/); netboot keeps
no template code of its own. What netboot decides is what a template sees and
where it is looked for:

- **Data**: every engine sees `ctx` (the `PixieContext`) and each of its
  fields at the top level (`target`, `image`, `dhcpzone`, `repos`,
  `resources`, `generated`, `version`, and every merged global), plus the
  helpers `shell_quote`, `Path` (**`pathlib_next.Path`**) and `Uri`
  (`pathlib_next.uri.UriPath`). The engines that cannot evaluate Python get it
  through a `tplx.DataEncoder(expose=["_id"])`, so `_id` reaches them
  (`%{IMAGE__ID}`, `@image['_id']`) while other `_`-prefixed names do not.
- **Names**: `ctx._template_names(name)` gives the candidates (MAC, hostname,
  IP, then the bare name; an unset IP and the null MAC are skipped) and
  `.searchpaths` the search paths, both passed to `tplx` for the template
  **and for every include inside it**. Name specificity outranks search-path
  order: a MAC-named file in the last search path beats a bare-named file in
  the first. The roots are `config["templates"]`, `./templates` by default.
- **The engine is chosen by suffix**: `.j2`/`.jinja`/`.jinja2` Jinja, `.mako`,
  `.liquid`, `.hbs`/`.handlebars`, `.mustache`, `.erb` (ruby), `.epp`
  (puppet), `.shtpl` the `%{NAME}` shell engine, anything else is copied as
  `bytes`. `templates_undefined` is tplx's `undefined`. The optional engines'
  libraries are netboot's extras of the same name, which install tplx's;
  `TPLX_RUBY` / `TPLX_PUPPET` name the programs when they are not on `PATH`.
- **Errors** are tplx's (`tplx.TemplateNotFound`, `tplx.UndefinedError`,
  `tplx.RenderError`, ...); the `pixie` CLI reports every `tplx.TplxError` as
  one line and exits 2.
- **A custom engine** is a `tplx.Template` subclass registered with
  `tplx.register_engine` (a `--load-module` plugin can do it); see tplx's own
  documentation for the contract.

## Utils (`netboot.utils`)

- **`Namespace`** (`netboot.utils.config`) — `yaconfiglib.TypedNamespace` +
  `OpaqueMerge`; the shared base for netboot's config objects (applies
  `_parse_<prop>` coercers at construction, and marks the built object as
  merge-opaque — a later config layer replaces it wholesale rather than
  merging field-by-field).
- **`flatten(map, _prefix="") -> dict`** — recursively flattens a
  dict/`Namespace`/list into a single-level dict, joining keys with `_`
  (`{"a": {"b": 1}}` → `{"a_b": 1}`) and using list indices as keys. Two
  sources that flatten to one key (a global `target_ip` beside
  `target.ip`) log a warning naming both values; the later one wins.
- **`shell_quote(text: str | list[str], quote="'") -> str | list[str]`** —
  quote value(s) so a POSIX shell reads them as literal text. `str` in → `str`
  out, `list` in → `list` out (element-wise); non-strings are stringified and
  `None` becomes `''`. The default single-quote form escapes an embedded `'`
  by closing/escaping/reopening, so nothing inside is special to the shell;
  `quote='"'` escapes only `"`, `\`, `` ` `` and `$`, leaving the shell's own
  expansion active — use it only when the template wants that. Any other
  `quote` raises `ValueError`.
- **`arr_get(arr, pos, default=None)`** — `arr[pos]` or `default` if out of
  range.
- **`import_(name: str) -> object`** — import `"pkg.mod.attr"` and return
  `attr` (splits on the last dot).
- **Network values are [`netimps`](https://pypi.org/project/netimps/)'s**,
  imported from it directly; netboot re-exports none of them. `PixieTarget.mac`
  is a `netimps.MACAddress` (`.format(sep=":", *, upper=False)`, `.hex()`;
  equal only to another `MACAddress`), `PixieTarget.ip` an `ipaddress`
  address, and `Repository.address` a `netimps.Host`. To fake DNS in a test,
  patch `netimps.resolve` (target hostnames) and `netimps.Host.ip` (repository
  addresses).

  The `dns` extra installs the `dnspython` resolver backend (`pip install
  netboot[dns]`) — best coverage, every record type, explicit `ns=`/`search=`
  control. It is **not** a no-op and `dnspython` does **not** arrive
  transitively: netimps >=0.2.0 makes it optional. Without it `resolve()`
  still works, falling back to netimps' `system` and `nslookup` backends,
  which serve address and reverse records only.

## Logging (`netboot.logging`)

- **`LOGGER`** — the `"netboot"` logger, named for the import package so it
  sits in the ordinary hierarchy (`logging.getLogger("netboot")` reaches it,
  `netboot.<child>` inherits from it). Importing netboot has **no logging side
  effects**: it configures nothing and imports no optional dependency.
- **`quiet_noisy_dependencies(insecure_warnings=False)`** — opt-in: turns
  `urllib3.connectionpool` / `paramiko.transport` down to `WARNING`, and with
  `insecure_warnings=True` also disables urllib3's `InsecureRequestWarning`
  **process-wide** (which is why that is off by default, and why it is a call
  rather than an import side effect). The `pixie` CLI calls it; an embedding
  application decides for itself.

## CLI driver (`netboot.main`)

Thin driver over `duho.run(..., exit="parse")` (so `duho.app`); `duho` owns command discovery, parser build,
config/env layering and parsing. Netboot overrides only *dispatch*: it loads
the layered YAML config into one `Pixie` object, then runs the selected
command against it.

- **`main(name=None, argv=None) -> int`** — build the app, parse `argv`,
  dispatch; returns the status, except that `--help`, `--version` and a usage error
  raise `SystemExit` (0, 0, 2); Ctrl-C returns
  130, a broken pipe 1. `name` defaults to `"pixie"` — the CLI's identity: it is the prog
  name **and** the `duho.env.Env` prefix, so `PIXIE_<KEY>` env vars (and an
  optional `pixie_env` companion module of defaults, autoloaded from
  `sys.path`/CWD) supply app settings; the resolved `Env` is attached to the
  dispatched instance as `_env_`. The `netboot` name is only the
  library/import package. This is the console-script
  (`pixie = netboot.main:main`) and `python -m netboot` entry point.
- **`PixieArgs`** (`duho.LoggingArgs` mixin) — the global CLI fields:
  `config` (`-c/--config`), `baseconfig` (default `./config`), `load_module`
  (`-l/--load-module`, repeatable, colon-extendable), `cmdspath`
  (`--cmdspath`, repeatable, `os.pathsep`-extendable).
- **`Pixie_`** (`PixieArgs` + `duho.Cli`) — the runnable app root;
  `_version_` is `netboot.__version__`.
- **`parse_path(path: str | Path) -> Path`** — a bare path → `LocalPath`; a
  path containing `:` (a URI scheme) → `UriPath`.
- Command-module contract (built-ins in `netboot.cmds`; discovered the same
  way via `--cmdspath` / `PIXIE_CMDS_PATH`, `os.pathsep`-separated): a module
  exposing `register(parser, args)` (add its argparse arguments) and
  `run(netboot: Pixie, args, conf: dict) -> int | None` (`conf` is the raw
  merged config dict, deep-copied before `Pixie` construction). A later
  command source wins on a name clash. A module whose `run` does **not**
  accept at least 3 positional params (no netboot-first signature) is instead
  dispatched through plain `duho.run_command(command, instance)`.
- Loading the config (`-c/--config`, else `<baseconfig>/pixie.yaml`) requires
  the `config` extra (`pyyaml`); raises `ImportError` with an install hint
  otherwise. `conf["templates"]` always gets the CWD's `templates` dir
  prepended, and a scalar `templates:` is accepted as a one-element list.
  An empty or comment-only file loads as `{}`; a top level that is not a
  mapping raises `PixieConfigError`.
- **Config loading is hardened**: the loader runs with `allow_commands=False`
  and `sandbox=True`, so `{{ ... }}` interpolation in config values renders in
  jinja2's sandbox and a document cannot run commands. Set
  `YACONFIGLIB_CONFINE_TO` to also restrict which directories `!include` may
  read from.
- **Exit codes**: 0 success, 1 the command reported failure (target not found,
  ambiguous), 2 a configuration or lookup error (reported as one line through
  the `netboot` logger; `-v` adds the traceback at DEBUG; with `DUHO_TRACEBACK`
  set the traceback is also printed to standard error and the status stays 2). A netboot-contract command returning
  a non-int, non-None value is warned about and treated as success rather than
  crashing after its work; a plain `run(args)` command's returned value is
  printed to standard output.

## Built-in commands (`netboot.cmds`)

- **`initiate <target> [--iscsi]`** — `netboot.lookup_target` then
  `netboot.initialize(target)`. `--iscsi` is accepted but not yet consumed by
  the built-in logic (a hook/plugin extension point). Exit 1 if the target
  isn't found or the query is ambiguous (both logged, not raised).
- **`complete <target>`** — `netboot.lookup_target` then
  `netboot.complete(target)`. Same exit-1 cases.
