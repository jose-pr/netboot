# Examples

A complete, runnable netboot setup. Nothing here touches real DHCP: the
`recording://` backend prints what it would arm, so the whole flow can be run on
a laptop.

```
config/pixie.yaml              the config `pixie` discovers (./config/pixie.yaml)
templates/debian/boot.cfg      shell-engine template (%{NAME} placeholders)
templates/debian/install.ks.j2 Jinja template (full context access)
plugins/recording.py           a DhcpServer backend that prints instead of arming
```

## Run it

From this directory, with netboot installed (`pip install -e ".[config]"` from
the repo root):

```sh
PYTHONPATH=plugins pixie -l recording initiate web01
PYTHONPATH=plugins pixie -l recording complete db01
```

`-l/--load-module recording` imports the plugin so the `recording://` scheme in
`dhcpzones.lan.dhcpservers` resolves to a backend; without it, building the
zone raises `ValueError` for the unknown scheme. `PYTHONPATH=plugins` is only
needed because the module lives in a subdirectory here — an installed plugin
package needs just `-l your.plugin`.

Expected output for `initiate web01`: the backend line, then the rendered
`boot.cfg`.

```
[dhcp] arm   web01 -> 10.0.0.10 (recording://dhcp-host)
...
    kernel vmlinuz
    append ip=10.0.0.10 hostname=web01 domain=example.com
```

## What each part demonstrates

- **Two ways to key a target.** `web01` is keyed by hostname and carries an IP;
  `aa:bb:cc:dd:ee:ff` is keyed by MAC. A MAC-keyed target has no IP to match a
  zone by containment, so it names its `dhcpzone` — otherwise zone lookup finds
  nothing. Either target can be selected by id, hostname, MAC or IP, in any MAC
  spelling.
- **Both template engines.** `boot.cfg` has no Jinja suffix, so the shell engine
  renders it: `%{UPPER_SNAKE}` placeholders from the flattened context, and a
  bare `%` left alone (which is what lets a kickstart's `%packages` through).
  `install.ks.j2` is Jinja, with `ctx`, `shell_quote`, `Path` and `Uri` in scope.
- **Repo URLs.** `ctx.repos["mirror"].service("tftp")` builds
  `tftp://10.0.0.2/debian` from the repo's address and service path. An
  `http`/`https` service would additionally need `pip install netboot[http]`.
- **Quoting.** `shell_quote("$6$salt$hash")` renders `'$6$salt$hash'` — the `$`
  sequences reach the installer intact instead of being expanded by the shell
  that reads the file.
