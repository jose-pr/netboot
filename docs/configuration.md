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
      # The scheme selects the DhcpServer backend. netboot ships none, so a
      # class named `dnsmasq` must be registered first -- see Extending -- and
      # `--load-module` must import it, or resolving this entry raises
      # ValueError for the unknown scheme.
      - dnsmasq://dhcp-host

# Where boot artifacts are fetched / served from.
repos:
  mirror:
    address: mirror.example.com
    services:
      http: http://mirror.example.com/debian
    local: /srv/mirror/debian
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
