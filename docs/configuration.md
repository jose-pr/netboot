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
| `windhcp://` | Windows DHCP | its own PowerShell cmdlets over ssh or WinRM | nothing over ssh; `netboot[winrm]` for WinRM |

Each has one thing that is easy to get wrong:

- **dnsmasq** re-reads `--dhcp-hostsfile`/`--dhcp-optsfile` only on SIGHUP, and
  never re-reads its main config — but `--dhcp-hostsdir`/`--dhcp-optsdir` pick up
  changed files by themselves, which is why a directory needs no `reload=` and a
  plain file does.
- **Kea** needs the `host_cmds` hook loaded *and* a writable hosts backend; a
  file-only Kea loads the hook and still refuses.
- **dhcpd** hosts added over OMAPI do not survive a restart by themselves — that
  is dhcpd's design.
- **Windows** needs the `DhcpServer` module on the host PowerShell runs on, and
  an account with DHCP-administrator rights.

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
back to the bare template name. A `.j2` / `.jinja` / `.jinja2` file is rendered
with Jinja2; anything else is rendered with the `%`-delimited shell engine, whose
`%{UPPER_SNAKE}` placeholders come from the flattened context.

Only the braced form is substituted, so a bare `%word` is left alone — a kickstart
file keeps its `%packages`, `%pre`, `%post` and `%end` sections, and a script keeps
`date +%Y`. Write `%%` for a literal `%` next to a brace, `%{NAME}` to substitute.
An unknown `%{NAME}` is an error.
