# `netboot` — public API header

Header-file-style reference for the `netboot` package: every public export with
its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is
self-contained. Development documentation lives with the source at
<https://github.com/jose-pr/netboot>.

Import the engine, its objects and the errors from `netboot` itself
(`from netboot import Pixie, PixieConfigError`); `netboot.dhcp`,
`netboot.content` and `netboot.logging` are public modules of their own;
`netboot.cli` is the command line, documented under "Command line", and
`import netboot` never imports it. A module or name starting with `_` is private.

`pip install netboot` is the engine and its DHCP and template layers
(`pydhcpctl`, `tplx`, `netimps`, `pathapi`, `uripath`, `anycfg`, `duho`).
Extras: `config` (`pyyaml`, which the `pixie` CLI needs to read a config file),
`dns` (the `dnspython` resolver backend), `mako`, `liquid`, `handlebars`,
`mustache` (those template engines, through tplx), `kea`, `dhcpd`, `winrm`,
`ssh` (those DHCP backends and transports, through dhcpctl), `docs`. Python 3.9+.

`netboot.__version__` is the installed distribution's version (`"0.0.0"` from
a source tree that is not installed).

## Engine (`netboot.engine`, re-exported from `netboot`)

`netboot.engine` exports the engine and its objects, and the package root
re-exports them, so `from netboot import Pixie` is the supported import and
`netboot.engine.Pixie` is the same object. `PixieTarget`, `PixieImage` and
`PixieContext` are defined in private modules; import them from either.

- **`Pixie(hooks=(), **config)`** — the engine (`hooks=None` is none). `config` is the merged config
  mapping: `targets`, `images`, `dhcpzones`, `repos` (each a `dict[id, ...]`
  built into the corresponding class via its type hints — `TypeError` from the
  value class falls back to a no-`_id` constructor call; a `None` entry
  (`targets: {host1:}` in YAML) means "all defaults"; a collection that is
  not a mapping, an id that is not text (YAML reads an unquoted
  `52:54:00:12:34:56` as a number), an entry that is neither a mapping nor an
  object, and a value its class cannot parse (a MAC, an address, a network)
  raise `PixieConfigError` naming `<collection>.<id>`), `globals` (dict;
  deep-copied at construction, so later edits to the caller's mapping do not
  reach the engine), `defaults` (per-collection default mappings), plus any other
  annotated `Pixie` attribute. A key nothing reads (`targetz:`, a zone's
  `gatway:`) is logged as a **warning** naming its dotted path; the keys read
  are each class's annotated fields plus its `_CONFIG_KEYS`, and a key
  starting with `_` is never warned about. `hooks` is a sequence of callables or
  `"module.func"` import-path strings; resolved once in `__new__`. Every
  config-driven step fires a `PixieEvent` through the hook chain (see below)
  so hooks can intercept object construction and property values before
  they're set.
  - **`.targets`** / **`.dhcpzones`** / **`.images`** / **`.repos`** —
    `dict[str, ...]` of `PixieTarget` / `DHCPZone` / `PixieImage` /
    `Repository`, keyed by config id.
  - **`.globals`** — `dict`, layered into every render context.
  - **`.templates_undefined`** — `"strict"` (default), `"lenient"` or
    `"debug"`, from the config key of the same name: what a template variable
    with no value does. `strict` raises naming it, `lenient` renders an empty
    string (the shell and mako engines also warn; Jinja, Liquid, Handlebars
    and Mustache do not), `debug` leaves the placeholder in the output. An
    unknown value raises
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
  - **`.lookup_image(name: str, target=None) -> PixieImage | None`** — the
    image whose `.match(img_name, name)` returns the highest truthy value
    (default `PixieImage.match` is exact-name equality); `None` if nothing
    matches. Fires `PixieEvent.FoundTargetImage`.
  - **`.lookup_dhcpzone(name: str, target=None) -> DHCPZone | None`** — by id;
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
    `config["templates"]`, and sets `.version`. A global named like a member
    of the context class (`target`, `image`, `version`, `render`,
    `searchpaths`, ... its annotated fields and public attributes) is
    **dropped with a warning**: it would replace the context's own. Raises `PixieLookupError`
    naming the target and what was configured if the image or dhcp zone cannot
    be resolved; `require_image=False` (what `.complete` uses) substitutes an
    empty `PixieImage` instead, so a machine can still be cleaned up after its
    image is retired from the config. Fires `PixieEvent.PixieContextForTarget`.
  - **`.initialize(target, **options) -> PixieContext`** — `make_context` then
    `ctx.pxe_init(self)` (arms every `dhcpzone.dhcpservers` for the target).
    Fires `StartPixieInitialize` / `EndPixieInitialize`, each with `options`
    as its hook kwargs (`initiate` passes `iscsi=`).
  - **`.complete(target, **options) -> PixieContext`** — `make_context` then
    `ctx.pxe_complete(self)` (disarms every `dhcpzone.dhcpservers`). Fires
    `StartPixieComplete` / `EndPixieComplete`, with `options` likewise.
  - **`.VERSION`** — class attr, `netboot.__version__` at class-definition time.

- **`PixieEvent`** (`StrEnum`) — hook event names: `NewPixieObject`,
  `StartPixieInit`, `SetPixieProperty`, `PixieInitiated`, `LookupTarget`,
  `FoundTarget`, `FoundTargetImage`, `FoundTargetDhcpzone`,
  `PixieContextForTarget`, `BuildDhcpOptions`, `StartPixieInitialize`,
  `EndPixieInitialize`, `StartPixieComplete`, `EndPixieComplete`.

- **`PixieTarget(**kwargs)`** (`argparse.Namespace` + `anycfg.OpaqueMerge`)
  — `_id`, `hostname`, `ip` (`IPAddress`, or `""` while unknown), `mac`
  (`MACAddress`; a number is refused with `PixieConfigError`), `image`,
  `dhcpzone`, `globals` (`dict`), `template_path` (`list[str | Path]`).
  Construction fills gaps: a MAC-shaped `_id` with no explicit `mac` is
  adopted as the MAC (else `mac` defaults to the null MAC
  `00:00:00:00:00:00`); an `_id` that looks like an IP fills `ip`, otherwise a
  non-MAC `_id` fills `hostname`. Construction does **no DNS**: a configured
  `ip` that is not an address raises netimps' `ValueError`, and so does a
  malformed `mac`. `hostname` is lower-cased.
  **`.resolve_ip() -> Self`** — if `ip` is unset and a hostname is known,
  **exactly one** forward lookup (`netimps.resolve`, no reverse lookup) fills
  it, the first time it is called only. A name that does not resolve —
  including when no resolver is reachable at all — leaves `ip` unset and logs
  a warning; it is never retried, and never raises. The engine calls it for a
  target it acts on: `lookup_target` (before `FoundTarget`), `make_context`,
  `lookup_dhcpzone`, and an IP query nothing configured matches, which looks
  up every hostname-only target. The `dns` extra is optional: without it netimps still
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
    carries on (a zone with no servers is a warning that nothing was
    armed); it raises only when **no** backend could be armed — the
    first error itself when it is a `PixieError` or `DHCPCtlError`, else a
    `PixieError` chained to it — and nothing is rolled back, so a target may end up armed on some
    servers and not others. `pxe_complete` never raises for a backend: each
    failure is logged as a warning and the rest are still disarmed.

## DHCP (`netboot.dhcp`)

The servers are **dhcpctl**'s (distribution `pydhcpctl>=0.1.0,<0.2`, its own shipped
`AGENTS.md` documents the backends, options, conditions and completion). This
module is what is about targets: zones, the layering of `dhcp_options` /
`dhcp_when` / `dhcp_complete`, and the secrets netboot reads. It re-exports
nothing from dhcpctl.

- **`DHCPZone(**kwargs)`** (`Namespace` + `OpaqueMerge`) — `network`
  (`IPNetwork`), `gateway` (`IPAddress | None`), `domain` (`str | None`),
  `search` (`list[str]`), `nameservers` (`list[IPAddress]`), `globals`,
  `dhcpservers` (`list[dhcpctl.DHCPServer]`; a string is built with
  `connect(uri)`, a server object is kept), and optionally `subnet_id` (Kea)
  and `scope` (Windows scope id). Accepts `gateway`+`netmask`, or a CIDR
  `network`/`gateway` string, and derives the rest; a `network` or `gateway`
  that does not parse raises netimps' `ValueError`. **`.nameserver`** — first of
  `.nameservers`, or `""`. **`.get_local_server(servers, default)`** — first
  server inside `.network`, else `default`; accepts strings and skips
  unparseable ones with a warning.
- **`connect(uri, codes=None, secrets=None) -> dhcpctl.DHCPServer`** —
  `dhcpctl.connect` with `owner="netboot"` (**`OWNER`**: dnsmasq region
  markers, Windows policy names `netboot-<mac>[-<name>]`, recipe comments) and
  `secrets`, which override the user or password the URI carries.
  **`server_from(entry, codes=None)`** — a `dhcpservers` entry as a server:
  a URI, a server object as it is, or a mapping of `uri:` and the secrets that
  server's backend takes (`URI_SECRETS` values: windhcp and kea `user` /
  `password`, dhcpd `keyname` / `omapi_key`; a `keyfile=` outranks
  `omapi_key`). Each server has its own; netboot reads no secret from the
  environment itself, while the config may (`password: "{{ env.NAME }}"`,
  interpolated when the file is loaded). An unknown key, or an entry that is
  neither, is a `PixieConfigError` that never shows a value. Logs, errors and
  `pixie dhcp-config` show a server as its URI with the password masked
  (`str(server)`). An unknown scheme or an unusable URI raises
  `PixieConfigError`. A backend for a new scheme is registered with
  `dhcpctl.register_backend(scheme, cls)` (e.g. from a `--load-module` plugin)
  or the `dhcpctl.backends` entry-point group.
- **`scope_for(zone) -> dhcpctl.Scope`** — `name` is the zone id, `network`,
  `subnet_id`, `scope_id` from the zone's `scope`.
- Options are pydhcp's `DHCPOptions`, keyed by code
  (`options[DHCPOptionCode.ROUTER]`, `options[3]`), each value read by its
  pydhcp codec. Config names are read with the codemap
  **`codes_for(dhcp)`** returns: the config's `dhcp.options` declarations over
  **`NETBOOT_CODES`** (`LEGACY_OPTION_NAMES` over pydhcp's), a
  `dhcpctl.option_codes` enum, so a declared type is the config's alone. The
  engine builds it as `Pixie.option_codes` before the zones, hands it to their
  servers, and reads `dhcp_options`, `dhcp_when` and `dhcp_complete` with it;
  `zone_defaults(zone, codes)` starts the bag every layer merges onto.
  `next-server` and `boot-file-name` in `dhcp_options` are the BOOTP header
  fields `siaddr` and `file` of what the target is served (a
  `dhcpctl.DHCPLease`), not options; `file`, `sname` and `siaddr` are read
  too, and `bootfile-name` is option 67 itself.
  **`LEGACY_OPTION_NAMES`** are the names netboot configs have always used
  (`domain-name-servers`, `boot-file-name`, `tftp-server-name`, `host-name`,
  `lease-time`), as aliases of pydhcp's names (`boot-file-name` of the `file`
  field). A hook receives the
  `DHCPOptions` and returns it or any mapping of option names; a value an
  option cannot hold is a `PixieConfigError`, naming the hook, image or
  target.
- **`zone_defaults(zone) -> dhcpctl.DHCPOptions`** — `router`,
  `domain-name-servers`, `domain-name`, `domain-search`, `subnet-mask`,
  `broadcast-address`, from what the zone knows.
- **`build_options(ctx, server) -> dhcpctl.DHCPOptions`** — merges, later
  winning: zone defaults → `image.dhcp_options` → `target.dhcp_options` → the
  `PixieEvent.BuildDhcpOptions` hook (`target=`, `server=` kwargs). An unknown
  option name, or a hook not returning a mapping, raises `PixieConfigError`. A
  server URI carries only how to reach the server.
- **`build_lease(ctx, server) -> dhcpctl.DHCPLease`** — what the target is
  served: its `ip` (`0.0.0.0` when it has none), `build_options`, and the
  header fields the image's and the target's `dhcp_options` set, the target's
  first. An address or field that cannot be read raises `PixieConfigError`.
- **`build_conditions(ctx) -> dict[str, dhcpctl.Condition]`** — `dhcp_when:`
  from zone → image → target, layered **by name** (a target redeclaring `ipxe`
  replaces the image's). **`build_completion(ctx) -> dhcpctl.Completion`** —
  `dhcp_complete:`, the most specific declaration wins; default removes the
  entry. A malformed block raises `PixieConfigError` naming the object.
- **`reservation_for(ctx, server) -> dhcpctl.Reservation`** — the target
  (`name` = `_id`, the null MAC becomes `None`), the zone's scope,
  `build_lease`, `build_conditions`, `build_completion`, and
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

- **`condition_recipe(ctx, server, condition) -> str`** — what `server` needs
  set up once for a `dhcp_when:` condition, in its own syntax: the text
  `pixie dhcp-config` prints.
- **`pixie dhcp-config [target] [--apply] [--as USER]`**
  (`netboot.cli.dhcp_config.DhcpConfig`) — prints each server's `condition_recipe()` with a
  header naming the server and condition, once per identical
  (server, condition, recipe); **contacts nothing** without `--apply`. With no
  target, a target whose zone cannot be found is a warning and the rest are
  still covered.
  `--apply` calls `ensure_condition` per server and returns 1 if any failed,
  having done the rest. `--as` implies `--apply` and prompts with `getpass`, only
  on a terminal (on Windows, a console handle: `NUL` reports `isatty()`) —
  without one it fails saying the password belongs in the
  server's `dhcpservers` entry, rather than blocking an unattended run. The
  user and password are that run's secrets for each server, held in memory,
  never a log, the environment or a script, and override any user and password
  the URI carries; a server whose backend takes no password is refused under
  `--as`. **The only command that
  prompts**; this is where privileged one-offs live so `initiate`/`complete` stay
  unattended.

## Content (`netboot.content`)

- **`Resource(**kwargs)`** — `path` (`Pathname`, coerced via `_parse_path`),
  `src` (repo id string). `resource / "sub"` joins the path, same `src`.
- **`Repository(**kwargs)`** — `address` (`netimps.Host`), `services`
  (`dict[str, URIPath]`), `local` (`URIPath | None`, a filesystem/local
  service; a Windows drive path such as `C:\tftp` is read as a path and becomes
  a `file:` URI, not URI scheme `c`). `repo / "sub"` (`.joinpath`) returns a **new** `Repository` with
  every service (and `.local`, when set) suffixed by `sub`, joined as a URI
  path so a service's query string stays last — the original is untouched.
  **`.get(*path, service=None) -> URIPath | None`** — join `path` onto the
  named service's base URI (`service=None` → `.local`, scheme `"file"`);
  `None` if that service isn't defined. **`.service(name) -> URIPath | None`**
  — the base URI for `name`. A full-URI service is used as written and
  reads no address. For a *relative* service path the authority is
  filled in from `.address`: resolved to an IP (`Host.ip(cache=True)`, so
  netimps' shared cache answers repeats; the name as written when it does
  not resolve) for every scheme
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
  the first. The roots are `config["templates"]`, a list or one root
  (through the CLI, `<config dir>/templates` by default).
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

- **`Namespace`** (`netboot.utils.config`) — `anycfg.TypedNamespace` +
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
  `urllib3.connectionpool`, `asyncssh` and `paramiko.transport` (whichever SSH
  library is in use) down to `WARNING`, and with
  `insecure_warnings=True` also disables urllib3's `InsecureRequestWarning`
  **process-wide** (which is why that is off by default, and why it is a call
  rather than an import side effect). The `pixie` CLI calls it; an embedding
  application decides for itself.

## CLI (`netboot.cli`)

Built on `duho.run(..., exit="parse")`; `duho` owns parser build, env
layering and parsing. Each built-in command is a class in its own module
(`netboot.cli.initiate.Initiate`, `.complete.Complete`, `.render.Render`,
`.dhcp_config.DhcpConfig`), a `duho.Cmd` whose `netboot` property builds the
engine from the global options on first use (a test sets `_netboot_`), and
whose `__call__` is the command. `netboot.main` re-exports `main`,
`PixieArgs`, `Pixie_` and `parse_path` from their former home.

- **`main(argv=None, *, name=None) -> int`** — build the app, parse `argv`,
  dispatch; returns the status, except that `--help`, `--version` and a usage error
  raise `SystemExit` (0, 0, 2); Ctrl-C returns
  130, a broken pipe 1. `name` defaults to `"pixie"` — the CLI's identity: it is the prog
  name **and** the `duho.env.Env` prefix, so `PIXIE_<KEY>` env vars supply
  app settings (no companion module is imported); the resolved `Env` is attached to the
  dispatched instance as `_env_`. The `netboot` name is only the
  library/import package. This is the console-script
  (`pixie = netboot.cli:main`) and `python -m netboot` entry point.
- **`PixieArgs`** (`duho.LoggingArgs` mixin) — the global CLI fields:
  `config` (`-c/--config`), `baseconfig` (default `./config`), `load_module`
  (`-l/--load-module`, repeatable, colon-extendable), `cmdspath`
  (`--cmdspath`, repeatable, `os.pathsep`-extendable).
- **`Pixie_`** (`PixieArgs` + `duho.Cli`) — the runnable app root;
  `_version_` is `netboot.__version__`.
- **`parse_path(path: str | Path) -> Path`** — text starting with an RFC 3986
  scheme of two or more characters and `:` (`sftp:`, `svn+ssh:`) → `URIPath`;
  anything else, a Windows drive (`C:\srv`) included → `LocalPath`.
- Plugin command contract (discovered via `--cmdspath` / `PIXIE_CMDS_PATH`,
  `os.pathsep`-separated, after the built-ins): a subclass of
  **`netboot.cli.Command`** (`.netboot` the engine, `.find(query)` the
  built-ins' lookup, `__call__` the command), or a module
  exposing `register(parser, args)` (add its argparse arguments) and
  `run(netboot: Pixie, args, conf: dict) -> int | None` (`conf` is the raw
  merged config dict, deep-copied before `Pixie` construction). A plugin
  shadows a built-in of its name, and `PIXIE_CMDS_PATH` wins over
  `--cmdspath`. A module whose `run` does **not**
  accept at least 3 positional params (no netboot-first signature) is instead
  dispatched through plain `duho.run_command(command, instance)`.
- Loading the config (`-c/--config`, else `<baseconfig>/pixie.yaml`) requires
  the `config` extra (`pyyaml`); raises `PixieConfigError` with an install
  hint otherwise. `conf["templates"]` is the config's `templates:` (a scalar is a
  one-element list), each **relative root resolved against the config file's
  directory** as `!include` is, an absolute path or URI used as given; with no
  `templates:` (or an empty one) it is `[<config dir>/templates]`. The working
  directory is never a root.
  An empty or comment-only file loads as `{}`; a top level that is not a
  mapping raises `PixieConfigError`.
- **Config loading is hardened**: `anycfg.Loader(merge="deep",
  recursive=True, interpolate=True, inject_env=True,
  policy=anycfg.Policy.untrusted())` — no command sources, no template files,
  and `{{ ... }}` in values rendered in tplx's sandbox. A malformed file is a
  `PixieConfigError` naming `file:line:col` (its cause an `anycfg.ParseError`),
  any other `anycfg.ConfigError` a `PixieConfigError` too, and a policy refusal
  an `anycfg.PolicyError` (a `PermissionError`). Set `ANYCFG_CONFINE_TO` to
  also restrict which directories `!include` may read from.

## Exceptions (`netboot.exceptions`, re-exported from `netboot`)

- **`PixieError(Exception)`** — base for what netboot raises on purpose, as
  opposed to a bug. The CLI reports these as one line and exits 2.
  - **`PixieLookupError(PixieError, LookupError)`** — a target, image or zone
    could not be resolved, or a target query was ambiguous.
  - **`PixieConfigError(PixieError, ValueError)`** — the configuration cannot
    be used as given (for example a top level that is not a mapping).

- A `dhcpctl.DHCPCtlError` (a server refusing, a URI it cannot use) and a
  `tplx.TplxError` (a template that cannot be found or rendered) come through
  as themselves; the CLI reports both as one line too.
- A value a config object cannot parse (a MAC, an address, a network) raises
  netimps' `ValueError` from the class itself, and `PixieConfigError` naming
  `<collection>.<id>` from `Pixie(...)`.

## Command line

- **`initiate <target> [--iscsi]`** — `netboot.lookup_target` then
  `netboot.initialize(target, iscsi=...)`. `--iscsi` is not consumed by the
  built-in logic: it reaches the Start/EndPixieInitialize hooks' kwargs. Exit 1 if the target
  isn't found or the query is ambiguous (both logged, not raised).
- **`complete <target>`** — `netboot.lookup_target` then
  `netboot.complete(target)`. Same exit-1 cases.
- **`render <target> NAME... [--out DIR]`** — `make_context(target)` then
  `ctx.render(NAME)` for each name, written to standard output, or to
  `DIR/NAME` with `--out`; a byte-exact copy is written as its bytes. Arms
  nothing. Same exit-1 cases; a template that cannot be found or rendered
  exits 2.
- **`dhcp-config [target] [--apply] [--as USER]`** — under DHCP above.

`pixie <command> --help` lists each command's arguments.

- **Exit codes**: 0 success, 1 the command reported failure (target not found,
  ambiguous), 2 a configuration or lookup error, a `--load-module` that does not
  import, or no DHCP server armed (reported as one line through
  the `netboot` logger; `-v` adds the traceback at DEBUG; with `DUHO_TRACEBACK`
  on the traceback is also printed to standard error and the status stays 2). A netboot-contract command returning
  a non-int, non-None value is warned about and treated as success rather than
  crashing after its work; a plain `run(args)` command's returned value is
  printed to standard output.

## Environment variables

| Variable | Read by | Effect |
| --- | --- | --- |
| `PIXIE_CMDS_PATH` | the CLI (duho) | more command directories or packages, `os.pathsep`-separated, after `--cmdspath` |
| `DUHO_TRACEBACK` | the CLI | when on (`1`, `true`, `yes`, `on`, `y`, `t`; `0`, `false`, `no`, `off`, `n`, `f` and empty are off), a reported error also prints its traceback to standard error; the status is unchanged |
| `ANYCFG_CONFINE_TO` | config loading (anycfg) | confines `!include` to those directories (`os.pathsep`-separated) |
| `TPLX_RUBY`, `TPLX_PUPPET` | tplx | the `ruby` / `puppet` program for `.erb` / `.epp`, when it is not on `PATH` |
| any `NAME` | config loading | `{{ env.NAME }}` in a config value reads it when the file is loaded |

netboot reads no credential from the environment by itself: a DHCP server's
secrets are its `dhcpservers` entry's, which may take them from `{{ env.NAME }}`.
The CLI imports no settings module (`Env(name, autoload=False)`): running it
in a directory runs no code from there unless `--load-module` names it.

## Gotchas

- **Quote MACs in YAML.** An unquoted all-digit MAC (`52:54:00:12:34:56`) is a
  base-60 number to YAML 1.1; netboot refuses it rather than arm another MAC.
- **A hostname-only target's `ip` is `""` until `resolve_ip()`**, which the
  engine calls for the target it acts on; iterate `.targets` yourself and you
  see unresolved targets.
- **`templates_undefined: lenient` warns only in the shell and mako
  engines**; Jinja, Liquid, Handlebars and Mustache render the empty string
  silently.
- **Arming is best effort**: `initialize` succeeds when any one server armed,
  and rolls nothing back on the others.
- **A config key nothing reads is a warning, not an error**: check the log
  after editing a config.
