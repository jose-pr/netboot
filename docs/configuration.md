# Configuration

netboot loads a YAML config (via [`anycfg`](https://github.com/jose-pr/anycfg),
so `!include` and deep-merge are available). By default it reads `pixie.yaml` from
`./config`; override with `--config FILE` or `--baseconfig DIR`.

The top-level keys map to the `Pixie` engine's collections:

```yaml
# Values shared into every render context.
globals:
  domain: example.com

# Machines to provision, keyed by an id (hostname / MAC / IP).
targets:
  web01:
    hostname: web01
    ip: 10.0.0.10
    mac: "52:54:00:12:34:56"   # quoted: an all-digit MAC is a YAML number
    image: debian
  "aa:bb:cc:dd:ee:ff":       # a MAC-keyed target
    image: debian
    dhcpzone: lan            # no ip to match a zone by containment, so name it

# What a target boots. template_path is searched for that image's templates.
images:
  debian:
    template_path: [debian]        # relative: searched inside the template root
    globals:
      kernel: vmlinuz

# The network a target lives on, plus its DHCP backend(s).
dhcpzones:
  lan:
    network: 10.0.0.0/24
    gateway: 10.0.0.1
    nameservers: [10.0.0.53]
    search: [example.com]
    dhcpservers:
      # The scheme selects the backend; the query says how to reach it.
      # See "DHCP servers and options" below.
      - dnsmasq:///?hostsfile=/etc/dnsmasq.d/netboot/hosts/&optsfile=/etc/dnsmasq.d/netboot/opts/

# Where boot artifacts are fetched / served from.
repos:
  mirror:
    address: mirror.example.com
    services:
      http: http://mirror.example.com/debian
    local: /srv/mirror/debian
```

## DHCP servers and options

Each entry under a zone's `dhcpservers` is a URI, or a mapping of `uri:` and
that server's secrets. Its **scheme** picks the backend, and its **query
string** says only how to reach the server: that backend's connection settings,
nothing else. What the server tells a client
comes from the zone, the image and the target. The backends are
[dhcpctl](https://github.com/jose-pr/dhcpctl)'s, and each netboot extra below
installs the matching dhcpctl one.

| Scheme | Server | How it applies a reservation | Needs |
| ------ | ------ | ---------------------------- | ----- |
| `dnsmasq://` | dnsmasq | writes `--dhcp-hostsfile`/`--dhcp-optsfile` entries, local or over `sftp://` | nothing locally; `netboot[ssh]` for remote paths |
| `kea://`, `keas://` | ISC Kea | `reservation-add` / `reservation-del` via the control agent | `netboot[kea]` |
| `dhcpd://` | ISC dhcpd | an OMAPI host object | `netboot[dhcpd]` |
| `windhcp://` | Windows DHCP | its PowerShell cmdlets *or* `netsh`, locally, over ssh or WinRM | `netboot[ssh]` over ssh, `netboot[winrm]` over WinRM |

### Credentials

A server's credentials are its own, given in its entry: Windows DHCP and Kea
take `user` and `password`, dhcpd `keyname` and `omapi_key` (a `keyfile=` in
the URI outranks `omapi_key`). A value in the entry overrides a user or
password written in the URI, so one URI can serve several logins. A value is written in the config, or produced when the file is loaded:
`{{ env.NAME }}` reads an environment variable, and anything else the config
loader offers works the same way. netboot reads no credential from the
environment by itself.

```yaml
dhcpservers:
  - uri: windhcp://dhcp01/?transport=winrm
    user: svc-netboot
    password: "{{ env.DHCP01_PASSWORD }}"
  - uri: dhcpd://dhcp02/?keyname=omapi_key
    omapi_key: "{{ env.DHCP02_OMAPI_KEY }}"
  - kea://kea01:8000/               # no credentials
```

An unknown key is a configuration error, and it never shows a value.

Each has one thing that is easy to get wrong:

- **dnsmasq** re-reads `--dhcp-hostsfile`/`--dhcp-optsfile` only on SIGHUP, and
  never re-reads its main config. `--dhcp-hostsdir`/`--dhcp-optsdir` read a new
  file by themselves but only ever add, so a directory arms with no `reload=`
  while completing a target SIGHUPs dnsmasq (`pidfile=`, default
  `/run/dnsmasq/dnsmasq.pid`, or `reload=` for a remote host); a plain file needs
  `reload=` every time. A target served options needs `optsfile=` too.
- **Kea** needs the `host_cmds` hook loaded *and* a writable hosts backend, or
  `?target=memory` on the URI to keep reservations in Kea's running
  configuration (Kea 2.7.4 or later; they are gone when Kea restarts); a Kea
  with neither loads the hook and still refuses.
- **dhcpd** writes a host added over OMAPI to its leases file, which is what
  keeps it across a restart: a dhcpd started with a fresh leases file has lost it.
- **Windows** validates some option values as it stores them, whichever method
  you use: a name server that does not answer is refused rather than stored. An
  arm that fails part-way leaves the reservation with the options applied so far
  — the next successful arm fixes it, and `pixie complete` removes it.
- **Windows** needs an account with DHCP-administrator rights, and — for the
  default `method=powershell` — the `DhcpServer` module on the host the shell runs
  on. Where that module is missing (Server Core without the RSAT feature, or an
  older release) `method=netsh` drives `netsh dhcp server ...` instead. The two
  choices are independent: `transport=` is how netboot gets a shell (`local`,
  `ssh` or `winrm`), `method=` is what it runs there. **`transport=local` runs
  PowerShell on the machine netboot is running on**, which with `server=` is the
  ordinary RSAT shape — run it here, act on that server — and needs no sshd, no
  WinRM and no credential. An empty host means local, because there is no host to
  reach: `windhcp:///?server=dhcp01`. A *named* host always uses a transport, even
  if the name happens to be this machine.

  netsh is still invoked *from* PowerShell, with each argument an element of a
  JSON array — so no value is ever spliced into a command line, which is the same
  guarantee the cmdlet path gives. Extra commands (a policy, say) go through
  a backend subclass's `extras()`; see
  [Extra commands and conditions](extending.md#extra-commands-and-conditions),
  which is also where the iPXE chainload recipe lives.

  **netsh cannot be trusted to report failure.** Given an option value it dislikes
  — a name server that does not answer — it keeps the rest, prints "not a valid DNS
  Server", prints "Command completed successfully" and exits 0 (measured on
  Windows Server 2025). So netboot reads the state back afterwards with
  `netsh ... dump`, the one netsh output that is command syntax rather than
  localised prose, and fails naming any option that did not land or lost values.
  The cmdlet method needs no such check: Windows raises there by itself. A few behaviours are
  netsh's own rather than netboot's: an option's data type has to be declared
  (netboot maps the modelled options, and sends anything else as `STRING`), a
  multi-valued option is passed as one argument per value, and the reservation is
  created as `BOTH` (DHCP and BOOTP) to match what the cmdlets do by default.

### When provisioning finishes (`dhcp_complete:`)

A target that is still handed a boot file after its install finishes **boots
the installer again**. `pixie complete` removes the reservation, which stops that
— and also throws away the fixed address the rest of the estate may depend on.
`dhcp_complete:` chooses what the entry becomes instead:

```yaml
images:
  debian:
    dhcp_options: {boot-file-name: undionly.kpxe}
    dhcp_complete:
      keep: true                                 # default false = remove it
      options: {boot-file-name: sanboot.ipxe}     # omit for "no boot options"
```

| Config | Result |
| ------ | ------ |
| nothing (the default) | the reservation is removed, exactly as before |
| `keep: true` | the address and its options stay; the **boot** options go, so the client's firmware falls through to local disk |
| `keep: true` with `options` | those options are served instead — an iPXE script ending in `sanboot` hands control to the disk, which works on firmware with no local-disk fallback of its own |

Only the boot file (`boot-file-name`), `next-server`, the server name, and
options 66 and 67 (`tftp-server-name`, `bootfile-name`) are replaced or
removed. `router`, `domain-name-servers`, `subnet-mask` and the rest are what make
a kept reservation worth keeping, and none of them re-kicks a machine. `options`
**replaces** the boot options rather than merging with them — a merge would leave
the installer's in place, which is the whole problem.

It can be declared on a zone, an image or a target, and the most specific wins.
A kept entry also **loses its `dhcp_when` membership**, or the condition would go
on serving the installer to a finished machine.

Every shipped backend can keep an entry: `windhcp://` sets and removes option
values on the reservation, `dhcpd://` supersedes the host in one call, `kea://`
upserts the reservation (so a failure leaves the old one rather than none), and
`dnsmasq://` rewrites that target's region in the files it already owns. A plugin
backend that cannot says so by not implementing `keep_target`, and the failure is
logged per server like any other.

### Conditional options (`dhcp_when:`)

Some boot decisions depend on **who is asking**. The standard case is chainloading
iPXE: a PXE ROM must be handed the iPXE binary, and iPXE itself must then be
handed a script — serve the binary to both and iPXE loads iPXE forever. No
per-target option can express that, because both requests come from the same
target; the *server* has to answer differently depending on the client.

`dhcp_when:` is a **mapping keyed by name**, and the name is what each DHCP server
calls its own construct — a Windows policy, a dhcpd group, a Kea client class:

```yaml
images:
  debian:
    dhcp_options:
      boot-file-name: undionly.kpxe        # what a PXE ROM gets
    dhcp_when:
      ipxe:                                # -> policy / group / class "ipxe"
        match: {user-class: iPXE}          # what iPXE sends
        options: {boot-file-name: boot.ipxe}
```

It can be declared on a zone, an image or a target, and layers **by name**, so a
target redeclaring `ipxe` replaces the image's instead of adding a second
condition that also matches. Keying by name is also what makes two targets share
one policy rather than create two.

`match` accepts `user-class` (option 77) and `vendor-class` (option 60) — the tests
every backend can express. `options` takes the same option names as
`dhcp_options`.

| Backend | Construct | netboot creates it | If it cannot |
| ------- | --------- | ------------------ | ------------ |
| `windhcp://` | a scope-level **policy** per target (plus the user/vendor **class** it references), and a base policy for the same target | yes, through the cmdlets — **netsh has no policy support at all**, so this part ignores `method=` | prints the `Add-DhcpServerv4Class` / `Add-DhcpServerv4Policy` lines to run once as an admin |
| `dhcpd://` | an inline **`if`** in the host's statements (default), or a named **group** with `conditions=group` | yes — statements always, groups over OMAPI | prints the `group { }` block for `dhcpd.conf` |
| `kea://` | a **client class** of the target's own (`<name>-<mac>`), plus one serving what the condition overrides when it does not match | only with the `class_cmds` hook (`class-add`) | prints the `client-classes` JSON for `kea-dhcp4.conf` |
| `dnsmasq://` | — | **no** | refuses, and prints the `dhcp-match`/`dhcp-option` pair for `dnsmasq.conf` |

#### Windows: why a condition means two policies

A Windows DHCP **reservation's options outrank every policy** — the order is
reservation, scope policy, server policy, scope, server, applied per option. A
client that takes option 67 from its reservation *ignores* option 67 from any
policy. So netboot **keeps an option a condition overrides off the reservation**
and serves it from policies instead: one matching the condition's test, and a base
one carrying what the reservation would have said. Options no condition touches
stay on the reservation, where that precedence is a feature.

The two policies are matched on the target's **MAC** as well, because a policy is
scope-level: without the MAC it would answer for every client in the scope.
Processing order is set explicitly, conditional before base, or the base policy
would answer an iPXE client and the chainload would never happen. Completing the
target removes both, whether the reservation is removed or kept.

`conditions=shared` is the other shape: one policy per condition name, matched on
the test alone, for every client in the scope. netboot still keeps the option off
the reservation, but the base value is then **not netboot's to serve** — a
scope-wide base policy would answer for every client, so it comes from a scope
option or a policy of your own. A shared policy that exists is used as it is, and
is never removed. Use it where netboot cannot create policies at all (no
PowerShell module, so `method=netsh`): an administrator creates one policy per
condition **once**, rather than one per machine forever, and netboot trusts it to
be there.

```
windhcp://dhcp01/?conditions=shared     # an admin pre-creates the policies
windhcp://dhcp01/                       # netboot manages a pair per target
```

Creating a reservation already needs DHCP-administrator rights, so in the default
`target` mode netboot creating the policies costs no privilege it did not already
have.

**A backend that cannot apply a condition fails rather than arming without it.** A
target that silently misses its chainload boots the installer again, which is
worse than an error. That is safe because arming is best effort per server: the
zone's other servers are still armed, the failure is logged, and the run fails
only if **no** server could be armed. A zone whose only server refuses therefore
fails, which is the correct reading of "this config cannot be delivered here".

When netboot cannot *create* the construct — no `class_cmds` hook, no DHCP-admin
rights, a Windows host without the PowerShell module — the error carries the exact
commands or config to apply. Once an administrator has run them, netboot finds the
construct by name and needs no privileges of its own.

`pixie dhcp-config` prints that configuration for every server a config names,
without contacting anything, so it can be read, reviewed and pasted into a change
ticket. `--apply` runs it, and `--as <user>` prompts for that account's password
— the **only** place netboot prompts, because `initiate` runs unattended from
hooks and would otherwise hang a pipeline. See [the CLI reference](cli.md).

### Options

Options are set in `dhcp_options` on an image or a target (and the zone
supplies its own, below), named as [pydhcp](https://github.com/jose-pr/pydhcp)
names them (`router`, `dns`,
`domain-name`, `domain-search`, `ntp-servers`, `subnet-mask`, `bootfile-name`,
... in any case, with `-` or `_`), by the older names netboot always took
(`domain-name-servers`, `boot-file-name`, `tftp-server-name`, `host-name`,
`lease-time`), by a name the config declares (below), or as `option-<n>`. Each
value is checked against the option's type before any server is contacted —
`router: gateway` is a configuration error, not a reservation Kea refuses. Give
a list option a YAML list, and an option with no type of its own
(`option-224`, unless declared) its octets as hex text (`0a0b`, `0a:0b`) or
`{hex: 0a0b}`. Backend-native text comes from a backend subclass (see the
extending guide).

`next-server` and `boot-file-name` are not options but the BOOTP header
fields `siaddr` and `file`, as `file`, `sname` and `siaddr` are. They are
still written in `dhcp_options`, and each backend sets them as its server
spells the fields (Kea's `boot-file-name`, dhcpd's `filename`, dnsmasq's
`dhcp-boot`; Windows, which has no such fields per reservation, gets the boot
file as option 67). `bootfile-name` is option 67 itself, sent as an option:
some ROMs read that, some the header field; set both if unsure.

#### Declaring option names (`dhcp.options`)

The top-level `dhcp.options` section names options a config uses that pydhcp
has no name or type for, and aliases of its own:

```yaml
dhcp:
  options:
    site-tag: {code: 224, type: String}   # an option of your own: its code, and the pydhcp type of its value
    gw: router                            # an alias of a name...
    pxe-file: 67                          # ...or of a code
    tag: site-tag                         # ...or of another declared name
```

`type` is a pydhcp codec name (`String`, `U8`, `U16`, `U32`,
`IPv4AddressOption`, `Boolean`, ...) or `List[...]` of one; without a `type`,
pydhcp's own is used, and an option pydhcp has no type for takes only bytes.
Every place that names an option reads these: `dhcp_options`, `dhcp_when`
and `dhcp_complete`.

Options are merged in this order, later winning:

1. what the zone already knows (`gateway` → `router`, `nameservers`, `domain`…);
2. `images.<id>.dhcp_options`;
3. `targets.<id>.dhcp_options`;
4. any hook on `PixieEvent.BuildDhcpOptions` (a `hooks:` entry), which gets the
   options, the target and the server, and returns the options to serve.

`subnet_id` (Kea) and `scope` (Windows) belong to the **zone**.

```yaml
dhcpzones:
  lan:
    network: 10.0.0.0/24
    subnet_id: 44                      # kea; windhcp uses `scope`
    dhcpservers:
      - kea://10.0.0.1:8000/
images:
  debian:
    dhcp_options: {boot-file-name: pxelinux.0, next-server: 10.0.0.2}
```

## Where templates are looked for

`templates` is the list of **template roots**. A relative root is resolved
against the **config file's directory**, the same base `!include` uses, and with
no `templates:` at all the root is `templates` beside the config file
(`./config/templates` for the default `./config/pixie.yaml`). An absolute path or
a URI is used as given. The working directory is never a root, so a config means
the same thing whichever directory `pixie` runs from:

```yaml
templates: [../templates, /srv/netboot/shared]   # beside ./config, then absolute
```

An image's or target's `template_path` **extends** the search path; it does not
replace the roots, and the roots are always searched last:

- a **relative** entry is resolved inside each root, so `template_path: [debian]`
  means `<root>/debian`;
- an **absolute** path or a **URI** (`/srv/tftp/tpl`, `http://boot/tpl`) is used
  as given.

A repository's `src`/`path` on an image address its *artifacts* (kernel,
initrd), never its templates.

## Undefined template variables

`templates_undefined` is a top-level config key choosing what happens when a
template names a variable that has no value. It means the same thing in both
engines — before it existed, the shell engine raised while Jinja quietly
rendered an empty string, so the behaviour depended on a file's suffix.

| Value | Behaviour |
| ----- | --------- |
| `strict` (default) | Raise, naming the variable. A typo fails the run instead of shipping a boot artifact with a blank where a kernel path belongs. |
| `lenient` | Render an empty string. The shell and mako engines also log a warning naming the variable; Jinja, Liquid, Handlebars and Mustache do not. |
| `debug` | Leave the placeholder (`{{ name }}` / `%{NAME}`) in the output, so the gap is visible in the rendered file. |

```yaml
templates_undefined: lenient   # keep the pre-0.2 Jinja behaviour
```

## Interpolation, includes and trust

Config values may use `{{ ... }}` interpolation, evaluated while the config
loads: `greeting: "{{ globals.site }}-boot"` resolves against the merged
document. It renders in tplx's **sandbox**, and the loader runs with anycfg's
untrusted policy (no command sources, no template files), so a config document
cannot execute a command or reach out of the template language -- netboot never documented those capabilities and a config is
often assembled by `!include` from inventory exports rather than written by
hand.

`!include` can still read any file the process can read. To confine it, set
`ANYCFG_CONFINE_TO` to the directories includes may come from.

An empty or comment-only config loads as an empty mapping. A top level that is
not a mapping is an error naming the file.

## How values resolve

- **Targets** normalise `ip`/`mac`/`hostname` at load time. If a field is
  missing, netboot fills it in where it can (a MAC-shaped id becomes the `mac`; an
  IP-shaped id becomes the `ip`; a hostname is resolved to an IP via DNS).
- **Zones** derive `network` from a CIDR `gateway`, and coerce `nameservers` /
  `search` to lists. `dhcpservers` URIs are constructed into backends by scheme.
- **globals** are layered: engine globals, then per-image / per-zone / per-target
  `globals`, are merged into the render context (later wins).

## Templates

`templates` is a list of search paths (local or URI). For each render netboot looks
for a file named by the target's MAC (`aa-bb-cc-...`), hostname, or IP — falling
back to the bare template name.

An include inside a template (`{% include %}`, `<%include/>`, `{{> partial}}`, ...)
is found the same way, per target, and rendered by the engine its own suffix
names: a Jinja page can include a `.shtpl` fragment, which the shell engine
renders with the same context.

**The suffix picks the engine, and the stem names the artifact.** A `.j2` /
`.jinja` / `.jinja2` file is rendered with Jinja2; a `.shtpl` file with the
`%`-delimited shell engine, whose `%{UPPER_SNAKE}` placeholders come from the
flattened context. Seven more engines ship for template trees that already exist
in another language. They are [`tplx`](https://jose-pr.github.io/tplx/)'s, which
netboot renders through:

| Engine | Suffixes | Needs | `templates_undefined` |
| ------ | -------- | ----- | --------------------- |
| `tplx.JinjaTemplate` | `.j2` `.jinja` `.jinja2` | — | all three |
| `tplx.MakoTemplate` | `.mako` | `netboot[mako]` | `strict`, `lenient`; `debug` behaves as `lenient` |
| `tplx.LiquidTemplate` | `.liquid` | `netboot[liquid]` | all three (`debug` names the variable in the output) |
| `tplx.HandlebarsTemplate` | `.hbs` `.handlebars` | `netboot[handlebars]` | **always lenient** — the library cannot fail |
| `tplx.MustacheTemplate` | `.mustache` | `netboot[mustache]` | **always lenient** (logs under `strict`) |
| `tplx.ERBTemplate` | `.erb` | `ruby` on `PATH` (or `TPLX_RUBY`) | n/a — missing data is Ruby's `nil` |
| `tplx.EPPTemplate` | `.epp` | `puppet` on `PATH` (or `TPLX_PUPPET`) | n/a — missing data is Puppet's `undef` |
| `tplx.ShellTemplate` | `.shtpl` | — | all three |
| `tplx.CopyTemplate` | anything else | — | n/a — nothing is substituted |

Every optional engine is **registered whether or not its library is installed**,
so a `.liquid` file fails naming the extra to install (`tplx[liquid]`, which
`netboot[liquid]` installs) instead of being quietly copied. None of them is imported until a file claims it, so an install
that uses none pays nothing.

Jinja and mako evaluate Python, so a template reaches into `ctx` directly. The
data languages (liquid, handlebars, mustache) and the external ones (ERB, EPP)
get a **mapping view** of the same context instead: `{{ ctx.target.hostname }}`
and `{{ target.hostname }}` both resolve, and an address, a MAC or a path arrives
as the string a template would have printed. ERB and EPP receive JSON in which
`_`-prefixed names are left out, except `_id`. ERB additionally sets each top-level
name as an instance variable (`@target`), and EPP as a parameter (`$target`).

ERB and EPP run the real `ruby` and `puppet` as a subprocess, with the context
marshalled to JSON — which is the point: an existing `.erb` renders the way its
author tested it, rather than the way a reimplementation guesses. Set `TPLX_RUBY`
or `TPLX_PUPPET` if the program is installed but not on `PATH`. Because a template is also found by its stem, the file behind
an artifact called `boot.cfg` is `boot.cfg.shtpl` and `ctx.render("boot.cfg")`
still finds it.

Anything else is **copied as is** — a static `grub.cfg`, an EFI binary or a
license file is a template that needs no engine. The copy engine works in
**bytes**: the loader never decodes the file, so the result is byte-exact
whatever it holds (CRLF line endings, latin-1 text, something that is not text at
all) and `ctx.render()` returns `bytes` for it rather than `str`.

Before 0.3.0 the shell engine claimed every suffix, so a file that *does* carry
`%{NAME}` placeholders now ships them unrendered: rename it to `*.shtpl` once.
That is the one mistake a copy can hide, so a copy that still finds placeholders
logs a warning naming the rename.

Only the braced form is substituted, so a bare `%word` is left alone — a kickstart
file keeps its `%packages`, `%pre`, `%post` and `%end` sections, and a script keeps
`date +%Y`. Write `%%` for a literal `%` next to a brace, `%{NAME}` to substitute.
An unknown `%{NAME}` follows `templates_undefined` (above).

`%{NAME:-fallback}` renders `fallback` when `NAME` is unset **or empty**, and
`%{NAME-fallback}` only when it is unset — the two POSIX forms, so a template can
carry its own default instead of requiring the variable. One layer of `'`/`"`
quotes is stripped (`%{NAME:-'a default'}`), the fallback is literal text (no
nested placeholders, and no `}` inside it), and a placeholder that has a default
never fails whatever `templates_undefined` says. The other POSIX forms (`:=`,
`:?`, `:+`) are not implemented and stay literal text.

The engine is configured by subclassing, not by config, for a tree that uses
another convention:

```python
from tplx import ShellTemplate

class DollarTemplate(ShellTemplate):
    EXT = (".tpl", ".cfg")   # a single string is fine; `None` claims any suffix
    DELIMITER = "$"
    PATTERN = "all"          # 'braced' (default), 'unbraced', or 'all'
```

Register the class with `tplx.register_engine` (see
[Extending](extending.md#custom-template-engines)). Engines are consulted in
`PRIORITY` order, highest first, so a registered engine is asked before the
catch-all `CopyTemplate` whatever order they were registered in. A file that is
not valid UTF-8 raises `tplx.NoEngineError` when the engine that claims it needs
text; set `BINARY = True` on an engine to be handed the raw bytes instead, as
`CopyTemplate` does.
