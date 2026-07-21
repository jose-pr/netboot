# Configuration

netboot loads a YAML config (via [`yaconfiglib`](https://github.com/jose-pr/yaconfiglib),
so `!include` and deep-merge are available). By default it reads `netboot.yaml` from
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

# What a target boots. template_path is searched for that image's templates.
images:
  debian:
    template_path: [templates/debian]
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
      - dnsmasq://dhcp-host        # scheme selects the DhcpServer backend

# Where boot artifacts are fetched / served from.
repos:
  mirror:
    address: mirror.example.com
    services:
      http: http://mirror.example.com/debian
    local: /srv/mirror/debian
```

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
