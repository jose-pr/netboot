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
| `-v` / `-q` | Increase / decrease log verbosity (from duho's `LoggingArgs`) |

## Commands

### `initiate <target> [--iscsi]`

Look up the target, build its render context, and call `add_target` on every
DHCP backend in the target's zone.

Rendering is **not** something `initiate` does on its own: the built-in command
arms DHCP, and artifacts are produced by whatever renders `ctx.render(...)` —
a hook on `PixieEvent.EndPixieInitialize`, a `DhcpServer` backend, or your own
command. `--iscsi` is likewise a flag for such a hook to read: the built-in
logic accepts it and does nothing with it.

### `complete <target>`

Look up the target, rebuild its context, and call `remove_target` on every DHCP
backend in its zone. Post-boot cleanup beyond that is a hook's job
(`PixieEvent.EndPixieComplete`).

## Adding your own commands

Point `--cmdspath` (or the `PIXIE_CMDS_PATH` environment variable) at a package or
directory of command modules. A netboot command module exposes:

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
- A `pixie_env` Python module importable at startup (e.g. a `pixie_env.py` in
  the working directory) may ship settings as `UPPER_CASE` module variables.
  Precedence runs explicit `env` values (kwargs or runtime writes) > real
  `PIXIE_*` environment variables > `pixie_env` module defaults, so an exported
  variable always beats a shipped default.

Commands receive the resolved accessor as `args._env_`, so a custom command can
read its own `PIXIE_<KEY>` settings without touching `os.environ`.
