# CLI

The `pixie` command line (the CLI of the `netboot` library; PXE is
pronounced "pixie") is built on [`duho`](https://github.com/jose-pr/duho): it
discovers commands, layers YAML config, then runs the selected command against a
single `Pixie` engine built from that config.

```sh
# Initiate the PXE process for a target (arm DHCP):
pixie initiate my-host

# Complete it (disarm DHCP):
pixie complete my-host
```

A target argument is resolved by exact id first, then by an exact hostname
(case-insensitive), MAC or IP match anywhere in the table, and only then by a
hostname prefix. MAC input is parsed, so `aa:bb:cc:00:00:01`,
`AA-BB-CC-00-00-01` and `aabb.cc00.0001` are the same target. A query matching
more than one target is refused (exit 1) rather than guessed at, and an empty
argument matches nothing.

## Global options

| Option | Purpose |
| ------ | ------- |
| `-c, --config PATH` | Explicit config file; overrides discovery. A `.cfg`/`.ini` suffix is read as INI (configparser), so YAML content needs a YAML suffix |
| `--baseconfig DIR`  | Base config directory to search (default `./config`) |
| `-l, --load-module M` | Import module(s) before building netboot (config/hook deps) |
| `--cmdspath PATH` | Extra directories/packages to search for commands |
| `--loglevel [NAME:]LEVEL[,...]` | Set a logger's level: `--loglevel DEBUG` for netboot's own, `--loglevel dhcpctl:DEBUG,tplx:INFO` for others |
| `-v` / `-q` | Increase / decrease log verbosity; these move the level of netboot's own `netboot` logger, and `-v` also prints the traceback behind a reported error |

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | the command succeeded |
| 1 | the command reported failure — target not found, or the query matched several targets |
| 2 | the configuration or a lookup could not be used: missing or malformed config, a value that does not parse, unknown image or zone, a `--load-module` that does not import, or no DHCP server could be armed. Reported as a single line; run with `-v` for the traceback |

## Commands

### `initiate <target> [--iscsi]`

Look up the target, build its render context, and call `add_target` on every
DHCP backend in the target's zone.

Rendering is **not** something `initiate` does on its own: the built-in command
arms DHCP, and artifacts are produced by whatever renders `ctx.render(...)` —
a hook on `PixieEvent.EndPixieInitialize`, a DHCP backend, or your own
command. `--iscsi` is likewise a flag for such a hook to read: it arrives as
`kwargs["iscsi"]` in the `StartPixieInitialize` and `EndPixieInitialize`
hooks, and the built-in logic does nothing with it.

### `complete <target>`

Look up the target, rebuild its context, and call `remove_target` on every DHCP
backend in its zone. Post-boot cleanup beyond that is a hook's job
(`PixieEvent.EndPixieComplete`).

### `render <target> NAME... [--out DIR]`

Render each named artifact for the target, through the same template search a
hook's `ctx.render(NAME)` uses, and write it to standard output or, with
`--out`, to `DIR/NAME`. A file the copy engine claims is written byte for byte.
Nothing is armed; this is how to look at what a target would be served.

```sh
pixie render web01 boot.cfg                 # print it
pixie render web01 boot.cfg install.ks --out /srv/tftp/web01
```

### `dhcp-config [target] [--apply] [--as USER]`

Prints the server-side configuration the config needs but netboot may not be able
to create itself: a Windows DHCP policy and the user class it references, a
`dhcpd.conf` group, a Kea `client-classes` entry. One section per server per
`dhcp_when:` condition, in that server's own syntax.

With no arguments it covers every target, warning about and skipping one whose
zone cannot be found, and a recipe several targets share is printed once; name
one to narrow it. **Printing
contacts nothing**, so it is safe to run anywhere and the output can go straight
into a change ticket or a config-management repo.

```sh
pixie dhcp-config                 # what do my servers need?
pixie dhcp-config --apply         # make it so, as the current account
pixie dhcp-config --as admin      # ... prompting for an admin password
```

`--apply` runs the configuration; `--as USER` implies it and prompts for that
account's password. **This is the only command that prompts.** `initiate` and
`complete` run unattended — from hooks, from a provisioning system — so a prompt
there would hang rather than ask anyone; `--as` without a terminal fails, saying
the password belongs in the server's `dhcpservers` entry, instead of blocking.
The password is passed to the backend as data and never logged, stored, put in
the environment, or written into a generated script.

Exit code 1 if any server could not be configured, with the reason per server;
the others are still done.

Run it once per condition. Afterwards netboot finds each construct by name, so
routine arming needs none of those rights — which is the point of the command
existing separately.

## Adding your own commands

Point `--cmdspath` (or the `PIXIE_CMDS_PATH` environment variable) at a package or
directory of command modules. A command is a class, as the built-ins are:

```python
from netboot.cli import Command


class Hosts(Command):
    """List the configured targets."""

    _parsername_ = "hosts"

    def __call__(self) -> int:
        for target in self.netboot.targets.values():   # the engine, from the config
            print(target._id, target.ip)
        return 0
```

`self.netboot` is the engine built from `--config`/`--baseconfig` after
`--load-module`; `self.find(query)` looks a target up the way the built-ins do.
A module may instead expose:

```python
def register(parser, args):      # optional: add argparse arguments
    ...

def run(netboot, args, conf):       # required: the command body
    ...
```

A module that instead follows duho's plain `run(args)` contract is dispatched by
duho directly, so ordinary duho commands work too.

Both sources add to the built-in commands rather than replacing them. On a name
clash the more specific source wins: `PIXIE_CMDS_PATH` over `--cmdspath`, and
either over a built-in.

## Environment

App settings are read through `duho.env.Env("pixie")`, so they live under the
`PIXIE_` prefix:

- `PIXIE_CMDS_PATH` — extra command sources, `os.pathsep`-separated (see above).

No settings module is imported: running `pixie` in a directory never runs code
from it unless `--load-module` names it.

Commands receive the resolved accessor as `args._env_`, so a custom command can
read its own `PIXIE_<KEY>` settings without touching `os.environ`.
