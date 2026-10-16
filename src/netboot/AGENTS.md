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
  - **`.resource(name: str | Resource, service: str = None) -> URIPath | None`**
    — resolve a resource (by id, looked up in `.resources`, or a `Resource`
    directly) to a fetchable URI via its repo's `service`. `None` if the repo
    or resource can't be found.
  - **`.resource_repo(name: str) -> Repository | None`** — the `Repository`
    backing resource `name`.
  - **`.searchpaths -> list[Path]`** — `target.template_path +
    image.template_path`. These **extend** the engine-wide template roots
    (`config["templates"]`; through the CLI, `<config dir>/templates` by
    default) and are consulted before
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

The servers are **dhcpctl**'s (distribution `pydhcpctl>=0.1.0,<0.2`, its own shipped
`AGENTS.md` documents the backends, options, conditions and completion). This
module is what is about targets: zones, the layering of `dhcp_options` /
`dhcp_when` / `dhcp_complete`, and the secrets netboot reads. It re-exports
nothing from dhcpctl.

- **`DhcpZone(**kwargs)`** (`Namespace` + `OpaqueMerge`) — `network`
  (`IPNetwork`), `gateway` (`IPAddress | None`), `domain` (`str | None`),
  `search` (`list[str]`), `nameservers` (`list[IPAddress]`), `globals`,
  `dhcpservers` (`list[dhcpctl.DHCPServer]`; a string is built with
  `connect(uri)`, a server object is kept), and optionally `subnet_id` (Kea)
  and `scope` (Windows scope id). Accepts `gateway`+`netmask`, or a CIDR
  `network`/`gateway` string, and derives the rest. **`.nameserver`** — first of
  `.nameservers`, or `""`. **`.get_local_server(servers, default)`** — first
  server inside `.network`, else `default`; accepts strings and skips
  unparseable ones with a warning.
- **`connect(uri) -> dhcpctl.DHCPServer`** — `dhcpctl.connect` with
  `owner="netboot"` (**`OWNER`**: dnsmasq region markers, Windows policy names
  `netboot-<mac>[-<name>]`, recipe comments). A secret comes from the URI's
  user/password when it has them, else from the environment, read **when the
  backend asks**: windhcp `password` ← **`WINDHCP_PASSWORD_ENV_VAR`**
  (`PIXIE_WINDHCP_PASSWORD`), `user` ← **`WINDHCP_USER_ENV_VAR`**
  (`PIXIE_WINDHCP_USER`); dhcpd `omapi_key` ← **`DHCPD_OMAPI_KEY_ENV_VAR`**
  (`PIXIE_DHCPD_OMAPI_KEY`; a `keyfile=` outranks both). Logs, errors and
  `pixie dhcp-config` show a server as its URI with the password masked
  (`str(server)`). An unknown scheme or an unusable URI raises
  `PixieConfigError`. A backend for a new scheme is registered with
  `dhcpctl.register_backend(scheme, cls)` (e.g. from a `--load-module` plugin)
  or the `dhcpctl.backends` entry-point group.
- **`scope_for(zone) -> dhcpctl.Scope`** — `name` is the zone id, `network`,
  `subnet_id`, `scope_id` from the zone's `scope`.
- **`zone_defaults(zone) -> dhcpctl.DHCPOptions`** — `router`,
  `domain-name-servers`, `domain-name`, `domain-search`, `subnet-mask`,
  `broadcast-address`, from what the zone knows.
- **`build_options(ctx, server) -> dhcpctl.DHCPOptions`** — merges, later
  winning: zone defaults → the server URI's query options → `image.dhcp_options`
  → `target.dhcp_options` → the server's `options_builder=` (an import path or
  callable, `fn(ctx, options) -> options`) → the `PixieEvent.BuildDhcpOptions`
  hook (`target=`, `server=` kwargs). An unknown option name, or a builder/hook
  not returning a mapping, raises `PixieConfigError`. A builder returning a
  plain dict keeps the URI's `raw.*` fragments.
- **`build_conditions(ctx) -> dict[str, dhcpctl.Condition]`** — `dhcp_when:`
  from zone → image → target, layered **by name** (a target redeclaring `ipxe`
  replaces the image's). **`build_completion(ctx) -> dhcpctl.Completion`** —
  `dhcp_complete:`, the most specific declaration wins; default removes the
  entry. A malformed block raises `PixieConfigError` naming the object.
- **`reservation_for(ctx, server) -> dhcpctl.Reservation`** — the target
  (`name` = `_id`, the null MAC becomes `None`, an unset ip `None`), the zone's
  scope, `build_options`, `build_conditions`, `build_completion`, and
  `context=ctx`, so a backend subclass overriding `extras(reservation, phase)`
  can decide per target from `reservation.context`.
- **`arm(ctx, server)`** / **`disarm(ctx, server)`** — `server.add` /
  `server.complete` with that reservation (`complete` removes the entry, or
  keeps it per `dhcp_complete:`). A target with no MAC on a backend that keys by
  one raises `PixieLookupError`. The engine's `pxe_init` / `pxe_complete` call
  these per server: arming is best effort (raises the first error only when no
  server armed), disarming never raises. `BuildDhcpOptions` therefore fires once
  per server per arm/disarm.

Any other `dhcpctl.DHCPCtlError` (a server refusing, a backend's library not
installed) reaches the CLI as a one-line error with exit status 2.

- **`pixie dhcp-config [target] [--apply] [--as USER]`**
  (`netboot.cmds.dhcp_config`) — prints each server's `condition_recipe()` with a
  header naming the server and condition; **contacts nothing** without `--apply`.
  `--apply` calls `ensure_condition` per server and returns 1 if any failed,
  having done the rest. `--as` implies `--apply` and prompts with `getpass`, only
  on a TTY — without one it fails naming `PIXIE_WINDHCP_PASSWORD`, rather than
  blocking an unattended run. The password goes into the environment the
  secrets are read from, never a log or a script. **The only command that
  prompts**; this is where privileged one-offs live so `initiate`/`complete` stay
  unattended. `conditions_for(ctx)` and `recipe(ctx, server, condition)` are the
  module's helpers for the same output.

## Content (`netboot.content`)

- **`Resource(**kwargs)`** — `path` (`Pathname`, coerced via `_parse_path`),
  `src` (repo id string). `resource / "sub"` joins the path, same `src`.
- **`Repository(**kwargs)`** — `address` (`netimps.Host`), `services`
  (`dict[str, URIPath]`), `local` (`URIPath | None`, a filesystem/local
  service; a Windows drive path such as `C:\tftp` is read as a path and becomes
  a `file:` URI, not URI scheme `c`). `repo / "sub"` (`.joinpath`) returns a **new** `Repository` with
  every service (and `.local`) suffixed by `sub` — the original is untouched.
  **`.get(*path, service=None) -> URIPath | None`** — join `path` onto the
  named service's base URI (`service=None` → `.local`, scheme `"file"`);
  `None` if that service isn't defined. **`.service(name) -> URIPath | None`**
  — the base URI for `name`. For a *relative* service path the authority is
  filled in from `.address`: resolved to an IP (`Host.ip()`, or the name as
  written when it does not resolve) for every scheme
  except `https`, which keeps the configured name so TLS validation and
  name-based virtual hosts still work. An `address` of the form `host:port` or
  `[v6]:port` becomes a real host and port (**`netimps.split_host`**; a v6
  literal comes back unbracketed, which is what uripath's `Source` wants,
  and a malformed port raises `PixieConfigError` naming the repo) rather than a
  hostname containing an
  escaped colon, and a repo with no address warns. A service written as a full
  URI keeps its own authority untouched. `repo[path, service]` is sugar for `.get(path, service=service)`.
  `http`/`https` services need nothing extra: `uripath` sends them on the
  standard library (`pathapi-requests` swaps in a `requests` transport and is
  never required). A service with no authority, such as `.local`, is written
  `file:/srv/tftp`, with no empty `//`. `tftp` is not a scheme `uripath`
  implements: such a URI is built, joined and printed, which is all netboot does
  with it.

## Templates (`tplx`)

Rendering is done by [`tplx`](https://pypi.org/project/tplx/); netboot keeps
no template code of its own. What netboot decides is what a template sees and
where it is looked for:

- **Data**: every engine sees `ctx` (the `PixieContext`) and each of its
  fields at the top level (`target`, `image`, `dhcpzone`, `repos`,
  `resources`, `generated`, `version`, and every merged global), plus the
  helpers `shell_quote`, `Path` (**`pathapi.Path`**) and `Uri`
  (`uripath.URIPath`). The engines that cannot evaluate Python get it
  through a `tplx.DataEncoder(expose=["_id"])`, so `_id` reaches them
  (`%{IMAGE__ID}`, `@image['_id']`) while other `_`-prefixed names do not.
- **Names**: `ctx._template_names(name)` gives the candidates (MAC, hostname,
  IP, then the bare name; an unset IP and the null MAC are skipped) and
  `.searchpaths` the search paths, both passed to `tplx` for the template
  **and for every include inside it**. Name specificity outranks search-path
  order: a MAC-named file in the last search path beats a bare-named file in
  the first. The roots are `config["templates"]` (through the CLI,
  `<config dir>/templates` by default).
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
- **`shell_quote(value) -> str | list[str]`** — `shlex.quote`, so a POSIX
  shell reads the result as literal text; also a template global. A value
  with nothing special to the shell comes back as it is (`web01` stays
  `web01`), anything else single-quoted. A list or tuple is quoted
  element-wise and returns a list; a non-string is stringified; `None` is
  `''`.
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
  path containing `:` (a URI scheme) → `URIPath`.
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
  otherwise. `conf["templates"]` is the config's `templates:` (a scalar is a
  one-element list), each **relative root resolved against the config file's
  directory** as `!include` is, an absolute path or URI used as given; with no
  `templates:` (or an empty one) it is `[<config dir>/templates]`. The working
  directory is never a root.
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
