# CLI

The `pixie` command line (the CLI of the `netboot` library; PXE is
pronounced "pixie") is built on [`duho`](https://github.com/jose-pr/duho): it
discovers commands, layers YAML config, then runs the selected command against a
single `Pixie` engine built from that config.

```sh
# Initiate the PXE process for a target (render artifacts + arm DHCP):
pixie initiate my-host

# Complete it (post-boot cleanup, disarm DHCP):
pixie complete my-host
```

A target argument is resolved by exact id, or by hostname prefix / MAC / IP.

## Global options

| Option | Purpose |
| ------ | ------- |
| `-c, --config PATH` | Explicit config file (yaml/cfg); overrides discovery |
| `--baseconfig DIR`  | Base config directory to search (default `./config`) |
| `-l, --load-module M` | Import module(s) before building netboot (config/hook deps) |
| `--cmdspath PATH` | Extra directories/packages to search for commands |
| `-v` / `-q` | Increase / decrease log verbosity (from duho's `LoggingArgs`) |

## Commands

### `initiate <target> [--iscsi]`

Look up the target, build its render context, produce the netboot artifacts and
arm every DHCP backend in the target's zone. `--iscsi` prepares the target as an
iSCSI LUN.

### `complete <target>`

Run post-boot cleanup for the target and disarm its DHCP backends.

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

## Environment

App settings are read through `duho.env.Env("pixie")`, so they live under the
`PIXIE_` prefix:

- `PIXIE_CMDS_PATH` — extra command sources, `os.pathsep`-separated (see above).
- A `pixie_env` Python module importable at startup (e.g. a `pixie_env.py` in
  the working directory) may ship settings as `UPPER_CASE` module variables.
  Note: as of current duho, these seeded values take precedence over real
  `PIXIE_*` environment variables.

Commands receive the resolved accessor as `args._env_`, so a custom command can
read its own `PIXIE_<KEY>` settings without touching `os.environ`.
