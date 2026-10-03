# Configuration

netboot loads a YAML config (via [`yaconfiglib`](https://github.com/jose-pr/yaconfiglib),
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
      # The scheme selects the backend; the query carries what the server
      # should tell the client. See "DHCP servers and options" below.
      - dnsmasq:///?hostsfile=/etc/dnsmasq.d/netboot.d/&router=10.0.0.1

# Where boot artifacts are fetched / served from.
repos:
  mirror:
    address: mirror.example.com
    services:
      http: http://mirror.example.com/debian
    local: /srv/mirror/debian
```

## DHCP servers and options

Each entry under a zone's `dhcpservers` is a URI. Its **scheme** picks the
backend; its **query string** carries that backend's connection settings and the
DHCP options the server should give the client.

| Scheme | Server | How it applies a reservation | Needs |
| ------ | ------ | ---------------------------- | ----- |
| `dnsmasq://` | dnsmasq | writes `dhcp-host`/`dhcp-option` files, local or over `sftp://` | nothing locally; `netboot[ssh]` for remote paths |
| `kea://`, `keas://` | ISC Kea | `reservation-add` / `reservation-del` via the control agent | `netboot[kea]` |
| `dhcpd://` | ISC dhcpd | an OMAPI host object | `netboot[dhcpd]` |
| `windhcp://` | Windows DHCP | its PowerShell cmdlets *or* `netsh`, over ssh or WinRM | nothing over ssh; `netboot[winrm]` for WinRM |

Each has one thing that is easy to get wrong:

- **dnsmasq** re-reads `--dhcp-hostsfile`/`--dhcp-optsfile` only on SIGHUP, and
  never re-reads its main config — but `--dhcp-hostsdir`/`--dhcp-optsdir` pick up
  changed files by themselves, which is why a directory needs no `reload=` and a
  plain file does.
- **Kea** needs the `host_cmds` hook loaded *and* a writable hosts backend; a
  file-only Kea loads the hook and still refuses.
- **dhcpd** hosts added over OMAPI do not survive a restart by themselves — that
  is dhcpd's design.
- **Windows** validates some option values as it stores them, whichever method
  you use: a name server that does not answer is refused rather than stored. An
  arm that fails part-way leaves the reservation with the options applied so far
  — the next successful arm fixes it, and `pixie complete` removes it.
- **Windows** needs an account with DHCP-administrator rights, and — for the
  default `method=powershell` — the `DhcpServer` module on the host the shell runs
  on. Where that module is missing (Server Core without the RSAT feature, or an
  older release) `method=netsh` drives `netsh dhcp server ...` instead. The two
  choices are independent: `transport=` is how netboot gets a shell (`ssh` or
  `winrm`), `method=` is what it runs there.

  netsh is still invoked *from* PowerShell, with each argument an element of a
  JSON array — so no value is ever spliced into a command line, which is the same
  guarantee the cmdlet path gives. Extra commands (a policy, say) go through
  `raw.windhcp=` or a subclass's `extras()`; see
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

A target that still receives `boot-file-name` after its install finishes **boots
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

Only `boot-file-name`, `next-server` and `tftp-server-name` are replaced or
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
| `windhcp://` | a scope-level **policy** (plus the user/vendor **class** it references) | yes, through the cmdlets — **netsh has no policy support at all**, so this part ignores `method=` | prints the `Add-DhcpServerv4Class` / `Add-DhcpServerv4Policy` lines to run once as an admin |
| `dhcpd://` | an inline **`if`** in the host's statements (default), or a named **group** with `conditions=group` | yes — statements always, groups over OMAPI | prints the `group { }` block for `dhcpd.conf`, which also survives a restart where an OMAPI group does not |
| `kea://` | a **client class**, named by the reservation | only with the `class_cmds` hook (`class-add`) | prints the `client-classes` JSON for `kea-dhcp4.conf` |
| `dnsmasq://` | — | **no** | refuses, and prints the `dhcp-match`/`dhcp-option` pair for `dnsmasq.conf` |

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

### Options

Anything in the query that is not a connection setting is a client option:
`router`, `domain-name-servers`, `domain-name`, `domain-search`, `ntp-servers`,
`subnet-mask`, `broadcast-address`, `lease-time`, `vendor-class-identifier`, or
`option-<n>` for anything netboot does not model. Repeat a key for a list.
`raw.<backend>=` passes backend-native text through untranslated.

Options are merged in this order, later winning:

1. what the zone already knows (`gateway` → `router`, `nameservers`, `domain`…);
2. the server URI's query;
3. `images.<id>.dhcp_options`;
4. `targets.<id>.dhcp_options`;
5. an `options_builder=my.module.function` — `fn(ctx, options) -> options`;
6. any hook on `PixieEvent.BuildDhcpOptions`.

**Some things belong to the target, not the connection**, and netboot refuses
them in a URI with a message saying where they go: `subnet_id` and `scope` on the
**zone**, `boot-file-name`, `next-server`, `tftp-server-name` and `host-name` on
the **image** or **target**. A boot file pinned to a connection would hand every
target on that server the same one.

```yaml
dhcpzones:
  lan:
    network: 10.0.0.0/24
    subnet_id: 44                      # kea; windhcp uses `scope`
    dhcpservers:
      - kea://10.0.0.1:8000/?domain-name-servers=10.0.0.53
images:
  debian:
    dhcp_options: {boot-file-name: pxelinux.0, next-server: 10.0.0.2}
```

## Where templates are looked for

`templates` is the list of **template roots** — the CWD's `templates` directory
is always prepended, so `./templates` is the default root and a config that
names none still works.

An image's or target's `template_path` **extends** the search path; it does not
replace the roots, and the roots are always searched last:

- a **relative** entry is resolved inside each root, so `template_path: [debian]`
  means `./templates/debian`. A config therefore means the same thing whichever
  directory `pixie` runs from — it used to be resolved against the process's
  working directory, which is not where the config lives;
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
| `lenient` | Render an empty string, and log a warning. This is what Jinja templates did before. |
| `debug` | Leave the placeholder (`{{ name }}` / `%{NAME}`) in the output, so the gap is visible in the rendered file. |

```yaml
templates_undefined: lenient   # keep the pre-0.2 Jinja behaviour
```

## Interpolation, includes and trust

Config values may use `{{ ... }}` interpolation, evaluated while the config
loads: `greeting: "{{ globals.site }}-boot"` resolves against the merged
document. It renders in jinja2's **sandbox**, and the loader runs with commands
disabled, so a config document cannot execute a command or reach out of the
template language -- netboot never documented those capabilities and a config is
often assembled by `!include` from inventory exports rather than written by
hand.

`!include` can still read any file the process can read. To confine it, set
`YACONFIGLIB_CONFINE_TO` to the directories includes may come from.

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

**The suffix picks the engine, and the stem names the artifact.** A `.j2` /
`.jinja` / `.jinja2` file is rendered with Jinja2; a `.shtpl` file with the
`%`-delimited shell engine, whose `%{UPPER_SNAKE}` placeholders come from the
flattened context. Seven more engines ship for template trees that already exist
in another language:

| Engine | Suffixes | Needs | `templates_undefined` |
| ------ | -------- | ----- | --------------------- |
| `JinjaTemplate` | `.j2` `.jinja` `.jinja2` | — | all three |
| `MakoTemplate` | `.mako` | `netboot[mako]` | `strict`, `lenient`; `debug` behaves as `lenient` |
| `LiquidTemplate` | `.liquid` | `netboot[liquid]` | all three (`debug` names the variable in the output) |
| `HandlebarsTemplate` | `.hbs` `.handlebars` | `netboot[handlebars]` | **always lenient** — the library cannot fail |
| `MustacheTemplate` | `.mustache` | `netboot[mustache]` | **always lenient** (logs under `strict`) |
| `ERBTemplate` | `.erb` | `ruby` on `PATH` (or `PIXIE_RUBY`) | n/a — missing data is Ruby's `nil` |
| `EppTemplate` | `.epp` | `puppet` on `PATH` (or `PIXIE_PUPPET`) | n/a — missing data is Puppet's `undef` |
| `ShellTemplate` | `.shtpl` | — | all three |
| `CopyTemplate` | anything else | — | n/a — nothing is substituted |

Every optional engine is **registered whether or not its library is installed**,
so a `.liquid` file tells you to `pip install netboot[liquid]` instead of being
quietly copied. None of them is imported until a file claims it, so an install
that uses none pays nothing.

Jinja and mako evaluate Python, so a template reaches into `ctx` directly. The
data languages (liquid, handlebars, mustache) and the external ones (ERB, EPP)
get a **mapping view** of the same context instead: `{{ ctx.target.hostname }}`
and `{{ target.hostname }}` both resolve, an address or a path arrives as the
string a template would have printed, and the engine's own machinery
(`ctx._netboot_`, the renderer) is left out. ERB additionally sets each top-level
name as an instance variable (`@target`), and EPP as a parameter (`$target`).

ERB and EPP run the real `ruby` and `puppet` as a subprocess, with the context
marshalled to JSON — which is the point: an existing `.erb` renders the way its
author tested it, rather than the way a reimplementation guesses. Set `PIXIE_RUBY`
or `PIXIE_PUPPET` if the program is installed but not on `PATH`. Because a template is also found by its stem, the file behind
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
from netboot.templates.shell import ShellTemplate

class DollarTemplate(ShellTemplate):
    EXT = (".tpl", ".cfg")   # a single string is fine; `None` claims any suffix
    DELIMITER = "$"
    PATTERN = "all"          # 'braced' (default), 'unbraced', or 'all'
```

Register the class with `netboot.templates.register_template_type` (see
[Extending](extending.md#custom-template-engines)) or pass it in
`Loader(..., template_types=[...])`. Engines are consulted in `PRIORITY` order,
highest first, so a registered engine is asked before the catch-all
`CopyTemplate` whatever order they were registered in. The default is every
registered engine: `JinjaTemplate`, `ShellTemplate`, then `CopyTemplate`. Narrow
it — dropping the copy engine, or giving it a suffix list of its own — and a file
nothing claims raises `netboot.templates.TemplateEngineError`, naming the file and
what each engine handles. A file that is not valid UTF-8 raises the same error when the engine
that claims it needs text; set `BINARY = True` on an engine to be handed the raw
bytes instead, as `CopyTemplate` does.
