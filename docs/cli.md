# CLI

pixy's command line is built on [`duho`](https://github.com/jose-pr/duho): it
discovers commands, layers YAML config, then runs the selected command against a
single `Pixy` engine built from that config.

```sh
# Initiate the PXE process for a target (render artifacts + arm DHCP):
pixy initiate my-host

# Complete it (post-boot cleanup, disarm DHCP):
pixy complete my-host
```

A target argument is resolved by exact id, or by hostname prefix / MAC / IP.

## Global options

| Option | Purpose |
| ------ | ------- |
| `-c, --config PATH` | Explicit config file (yaml/cfg); overrides discovery |
| `--baseconfig DIR`  | Base config directory to search (default `./config`) |
| `-l, --load-module M` | Import module(s) before building pixy (config/hook deps) |
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

Point `--cmdspath` (or the `PIXY_PATH` environment variable) at a package or
directory of command modules. A pixy command module exposes:

```python
def register(parser, args):      # optional: add argparse arguments
    ...

def run(pixy, args, conf):       # required: the command body
    ...
```

A module that instead follows duho's plain `run(args)` contract is dispatched by
duho directly, so ordinary duho commands work too.
