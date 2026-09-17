# `netboot` — public API header

Header-file-style reference for the `netboot` package: every public export with
its signature, arguments, contract, and gotchas, so this module can be
consumed without reading its source. Kept current with the public API. For the
project overview, install instructions and CLI usage, see the shipped
`README.md`, or <https://github.com/jose-pr/netboot>.

## Engine (`netboot` / `netboot.__init__`)

- **`Pixie(hooks=(), **config)`** — the engine. `config` is the merged config
  mapping: `targets`, `images`, `dhcpzones`, `repos` (each a `dict[id, ...]`
  built into the corresponding class via its type hints — `TypeError` from the
  value class falls back to a no-`_id` constructor call), `globals` (dict;
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
    null MAC matches nothing. Raises `LookupError` when a query matches more
    than one target rather than picking one; an empty string matches nothing.
    Fires `PixieEvent.LookupTarget` (may substitute a `PixieTarget` directly)
    then `PixieEvent.FoundTarget`; `None` if nothing resolves to a
    `PixieTarget`.
  - **`.lookup_image(name: str, target=None) -> PixieImage`** — the image
    whose `.match(img_name, name)` returns the highest truthy value (default
    `PixieImage.match` is exact-name equality); falls back to an empty dict
    if nothing matches (**not** `None` — check truthiness carefully). Fires
    `PixieEvent.FoundTargetImage`.
  - **`.lookup_dhcpzone(name: str, target=None) -> DhcpZone | None`** — by id;
    if `name` is empty and `target` is given, uses `target.dhcpzone` or finds
    the zone whose `.network` contains `target.ip` (and caches the id back
    onto `target.dhcpzone`). Fires `PixieEvent.FoundTargetDhcpzone`.
  - **`.make_context(target, globals: list[dict] = None) -> PixieContext`** —
    resolves image + dhcp zone for `target`, merges
    `[self.globals, *globals, image.globals, dhcpzone.globals, target.globals]`
    (image/dhcpzone/target objects are shared across targets and never
    mutated) into a fresh `PixieContext`, attaches a new `Renderer`/`Loader`
    over `config["templates"]`, and sets `.version`. Raises a plain
    `Exception` if the target's image or dhcp zone can't be resolved. Fires
    `PixieEvent.PixieContextForTarget`.
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
  `PixieContextForTarget`, `StartPixieInitialize`, `EndPixieInitialize`,
  `StartPixieComplete`, `EndPixieComplete`.

- **`PixieTarget(**kwargs)`** (`argparse.Namespace` + `yaconfiglib.OpaqueMerge`)
  — `_id`, `hostname`, `ip` (`IPAddress`), `mac` (`MACAddress`), `image`,
  `dhcpzone`, `globals` (`dict`), `template_path` (`list[str | Path]`).
  Construction fills gaps: a MAC-shaped `_id` with no explicit `mac` is
  adopted as the MAC (else `mac` defaults to the null MAC
  `00:00:00:00:00:00`); an `_id` that looks like an IP fills `ip`, otherwise a
  non-MAC `_id` fills `hostname`; then, if `ip` is still unset and a hostname
  is known, **exactly one** forward lookup
  (`netboot.utils.net.resolve`, no reverse lookup) fills it. A name that does
  not resolve leaves `ip` unset and logs a warning — it is never retried, and
  never raises. `hostname` is lower-cased. The `dns` extra is optional: without
  it netimps still resolves through its system/`nslookup` backends.

- **`PixieImage(**kwargs)`** (`content.Resource`) — `template_path`,
  `globals`. **`.match(name: str, check: str)`** — override point for custom
  image-selection logic; default is `name == check`. Returning a comparable
  (e.g. `int`) instead of a bare bool lets `Pixie.lookup_image` prefer the
  best of several matches.

- **`PixieContext(**kwargs)`** (`argparse.Namespace`) — built by
  `Pixie.make_context`, not constructed directly. Fields: `target`, `image`,
  `dhcpzone`, `repos` (`dict[str, Repository]`), `resources`
  (`dict[str, Resource]`), `generated` (`datetime`, set at construction),
  `version` (`str`), `_renderer` (a `templates.Renderer`). **`resources` starts
  empty**: there is no `resources:` config key, so it is a slot for a hook (or
  a caller) to fill before rendering — `.resource()` returns `None` until
  something does. Template search paths are `.searchpaths` (target then image
  `template_path`), not a `templates` field.
  - **`.render(filename: str, strict=True) -> str | None`** — render a
    template found by name/suffix search (see `templates.Loader` below)
    against this context. `strict=True` (default) re-raises render errors;
    `strict=False` logs at DEBUG and returns `None`.
  - **`.resource(name: str | Resource, service: str = None) -> UriPath | None`**
    — resolve a resource (by id, looked up in `.resources`, or a `Resource`
    directly) to a fetchable URI via its repo's `service`. `None` if the repo
    or resource can't be found.
  - **`.resource_repo(name: str) -> Repository | None`** — the `Repository`
    backing resource `name`.
  - **`.searchpaths -> list[Path]`** — `target.template_path + image.template_path`,
    consulted (before the engine-wide `config["templates"]`) when resolving a
    template name.
  - **`.pxe_init(netboot) -> Self`** / **`.pxe_complete(netboot) -> Self`** —
    arm/disarm every `dhcpzone.dhcpservers` for this context; called by
    `Pixie.initialize`/`.complete`, not usually invoked directly.

## DHCP (`netboot.dhcp`)

- **`DhcpServer(uri: str)`** — base class for DHCP backends, dispatched by
  URI scheme: `DhcpServer("dnsmasq://...")` returns an instance of whichever
  registered subclass has a matching lowercased class name (any subclass
  depth, so a plugin may subclass an intermediate base). Raises `ValueError`
  if no subclass matches the scheme — import the plugin module first (e.g.
  via CLI `--load-module`). `.uri` holds the original URI.
  - **`.add_target(ctx: PixieContext)`** / **`.remove_target(ctx)`** — no-ops
    on the base class; a backend overrides these to actually arm/disarm.
- **`DhcpZone(**kwargs)`** (`Namespace` + `OpaqueMerge`) — `network`
  (`IPNetwork`), `gateway` (`IPAddress | None`), `domain` (`str | None`),
  `search` (`list[str]`), `nameservers` (`list[IPAddress]`), `globals`,
  `dhcpservers` (`list[DhcpServer]`, strings coerced via `DhcpServer(uri)`).
  Accepts `gateway`+`netmask`, or a CIDR `network`/`gateway` string, and
  derives the rest. **`.nameserver`** — first of `.nameservers`, or `""`.
  **`.get_local_server(servers, default)`** — first `server` contained in
  `.network`, else `default`.

## Content (`netboot.content`)

- **`Resource(**kwargs)`** — `path` (`Pathname`, coerced via `_parse_path`),
  `src` (repo id string). `resource / "sub"` joins the path, same `src`.
- **`Repository(**kwargs)`** — `address` (`Host`), `services`
  (`dict[str, UriPath]`), `local` (`UriPath | None`, a filesystem/local
  service). `repo / "sub"` (`.joinpath`) returns a **new** `Repository` with
  every service (and `.local`) suffixed by `sub` — the original is untouched.
  **`.get(*path, service=None) -> UriPath | None`** — join `path` onto the
  named service's base URI (`service=None` → `.local`, scheme `"file"`);
  `None` if that service isn't defined. **`.service(name) -> UriPath | None`**
  — the base URI for `name` (host filled in from `.address.try_ip()` for
  non-local services). `repo[path, service]` is sugar for `.get(path, service=service)`.
  An `http`/`https` service needs the **`http` extra** (`pip install
  netboot[http]`): pathlib_next reaches those schemes through `requests`, which
  `pathlib_next[uri]` does not install. Without it `.service()`/`.get()` raise
  `ImportError` naming the extra; `file`/`tftp` services and rendering need
  nothing extra.

## Templates (`netboot.templates`)

- **`Loader(searchpaths, template_types=(JinjaTemplate, ShellTemplate))`** — a
  Jinja2 `BaseLoader`. **Name specificity outranks search-path order**: the
  loader walks the candidate names `ctx._template_names(...)` yields (MAC,
  hostname, IP, then the bare name) and, for each one, scans every search path
  (`ctx.searchpaths` then `searchpaths`) before moving to the next name — so a
  MAC-named file in the *last* search path beats a bare-named file in the
  first. Within one directory an exact filename match wins; otherwise a
  *stem* match does (`boot` matches `boot.j2`), resolved lowest-name-first so
  the choice never depends on filesystem listing order. Templates are read as
  UTF-8. A name may carry `k=v;flag:` options before the final `:` (parsed but
  not currently consulted by name selection). Picks the first
  `template_types` entry whose `.can_process(path, source)` is true; raises
  `jinja2.TemplateNotFound` if nothing matches, or a plain `Exception` if a
  matching type has no usable engine.
- **`Renderer`** — alias for `jinja2.Environment`; one is created per
  `PixieContext` (never share one across contexts — `globals["ctx"]` is
  mutated per render).
- **`Template`** — minimal base (`.render(**globals)`, classmethod
  `.can_process(file, template) -> bool`, both no-ops/`False` on the base).
- **`JinjaTemplate`** (`.j2`/`.jinja`/`.jinja2` suffix) — a real
  `jinja2.Template`; `.render()` additionally injects `shell_quote`, `Path`
  (**`pathlib_next.Path`**, not the stdlib's — it accepts URI paths too) and
  `Uri` (`pathlib_next.uri.UriPath`) into the render globals. These reach the
  rendered template only; a `{% import %}`ed macro file does not see them
  unless imported `with context`.
- **`ShellTemplate`** (matches any suffix — keep it **last** in
  `template_types`) — `string.Template` with `%`-delimited placeholders;
  `.render()` flattens the context (`utils.flatten`, keys joined with `_`,
  list items by index) into `UPPERCASE` substitution variables (`None` → `""`,
  `bool` → `"true"`/`"false"`). **Only the braced form `%{NAME}` is
  substituted**: a bare `%word` is literal text, which is what lets kickstart
  files (`%packages`, `%pre`, `%post`, `%end`) and `date +%Y` render
  untouched. `%%` yields a literal `%`, and an unknown `%{NAME}` raises
  `KeyError`.

## Utils (`netboot.utils`)

- **`Namespace`** (`netboot.utils.config`) — `yaconfiglib.TypedNamespace` +
  `OpaqueMerge`; the shared base for netboot's config objects (applies
  `_parse_<prop>` coercers at construction, and marks the built object as
  merge-opaque — a later config layer replaces it wholesale rather than
  merging field-by-field).
- **`Host(address: str | Host | None = None)`** — a hostname-or-IP repo
  address. **`.try_ip() -> IPAddress | str`** — resolves to an `IPAddress`
  (an IP literal as-is, a hostname through one forward lookup); falls back to
  the original string on resolution failure. The `dns` extra is optional —
  without it netimps resolves through its system/`nslookup` backends.
  Equality/hash by `.address`.
- **`flatten(map, _prefix="") -> dict`** — recursively flattens a
  dict/`Namespace`/list into a single-level dict, joining keys with `_`
  (`{"a": {"b": 1}}` → `{"a_b": 1}`) and using list indices as keys.
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
- **IP/MAC re-exports** from [`netimps`](https://pypi.org/project/netimps/),
  available as `netboot.utils.net.*`. `netboot.netutils` is the same module
  bound as an **attribute** of the package (what tests monkeypatch), not an
  importable path: use `from netboot.utils import net as netutils`, since
  `import netboot.netutils` raises `ModuleNotFoundError`.
  - `IPAddress`, `IPInterface`, `IPNetwork` — the v4/v6 **union types** you
    annotate with. They are not callable; build values with
    `parse(value, IPNetwork)`.
  - `parse(value, type, **kw)` raises on bad input; `try_parse(value, type)`
    returns `None` instead; `is_valid(value, type)` returns a bool. Networks
    parse non-strict, so `"10.0.0.5/24"` normalises rather than raising.
  - `MACAddress` — colon/hyphen/Cisco-dot/bare text, `int` or `bytes`;
    `.as_str(sep)`, `.packed`, `.oui`, hashable and ordered.
  - `resolve(query, rdtype=None)` → a list of **native** records (`A`/`AAAA`
    are `IPv4Address`/`IPv6Address`, not strings); `[]` on a genuine lookup
    failure, but a malformed query or unknown record type raises `ValueError`
    rather than looking like "no such record". `rdtype=None` auto-selects:
    `"ptr"` when `query` is an address literal, `"a"` otherwise.
  - `ping(dst)` → a `PingResult` that is truthy on success and also carries
    `.rtt_ms`/`.ttl`.
  - `get_interfaces()` / `iter_addresses()` — real adapter enumeration with
    true prefix lengths and MTU.

  The `dns` extra installs the `dnspython` resolver backend (`pip install
  netboot[dns]`) — best coverage, every record type, explicit `ns=`/`search=`
  control. It is **not** a no-op and `dnspython` does **not** arrive
  transitively: netimps >=0.2.0 makes it optional. Without it `resolve()`
  still works, falling back to netimps' `system` and `nslookup` backends,
  which serve address and reverse records only.

## Logging (`netboot.logging`)

- **`LOGGER`** — the `"NETBOOT"` logger. Importing this module also quiets
  `urllib3.connectionpool` / `paramiko.transport` to `WARNING` and disables
  urllib3's insecure-request warning, best-effort. Setting those levels imports
  nothing, but disabling the warning does `import urllib3` inside a
  `try/except`, so importing `netboot` imports urllib3 when it is installed —
  and that warning is disabled **process-wide**, for the host application too.

## CLI driver (`netboot.main`)

Thin driver over `duho.app`; `duho` owns command discovery, parser build,
config/env layering and parsing. Netboot overrides only *dispatch*: it loads
the layered YAML config into one `Pixie` object, then runs the selected
command against it.

- **`main(name=None, argv=None) -> int`** — build the app, parse `argv`,
  dispatch. `name` defaults to `"pixie"` — the CLI's identity: it is the prog
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
  prepended.

## Built-in commands (`netboot.cmds`)

- **`initiate <target> [--iscsi]`** — `netboot.lookup_target` then
  `netboot.initialize(target)`. `--iscsi` is accepted but not yet consumed by
  the built-in logic (a hook/plugin extension point). Exit 1 if the target
  isn't found or the query is ambiguous (both logged, not raised).
- **`complete <target>`** — `netboot.lookup_target` then
  `netboot.complete(target)`. Same exit-1 cases.
