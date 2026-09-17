# netboot — working in this checkout

Contributor orientation. The **API** is documented in
[`src/netboot/AGENTS.md`](src/netboot/AGENTS.md), which ships inside the wheel —
read that to *use* the library, this to *work on* it.

## Names

Deliberate, not drift: **`netboot`** is the library (distribution, import
package, `python -m netboot`), **`pixie`** is the CLI identity (console script,
prog name, `PIXIE_*` env vars, `pixie.yaml`), and **`Pixie*`** is the public
class prefix (`Pixie`, `PixieTarget`, `PixieContext`, `PixieEvent`). PXE is
pronounced "pixie". `netboot` remains the conventional variable name for an
engine instance, which is why command modules take `run(netboot, args, conf)`.

## Layout

| Path | What |
| ---- | ---- |
| `src/netboot/` | the package; `__init__.py` holds the engine |
| `src/netboot/cmds/` | built-in CLI commands (`initiate`, `complete`) |
| `tests/` | pytest suite; nothing here touches the network |
| `benchmarks/` | micro-benchmarks + committed results ([README](benchmarks/README.md)) |
| `examples/` | a runnable config, templates and a DHCP plugin ([README](examples/README.md)) |
| `docs/` + `mkdocs.yml` | the documentation site |

## Environments

Per-version venvs, gitignored: `.venv/<version>-<os>-<arch>/`. Develop on the
latest Python and also run the floor from `requires-python` (3.9) before
finishing a chunk of work.

```sh
python -m venv .venv/3.14-nt-amd64
.venv/3.14-nt-amd64/Scripts/python -m pip install -e ".[dev,docs,config,http]"
```

Install **every extra that has tests**: without `http` the repository-service
tests skip rather than run, and `pytest -rs` is how you see that happen.

## Commands

```sh
<venv>/python -m pytest -q -rs                 # the suite (skips are visible)
<venv>/python -m black src tests benchmarks    # formatter; run before committing
<venv>/python -m mkdocs build --strict         # docs must build clean
<venv>/python -m build                         # sdist + wheel
<venv>/python benchmarks/bench_netboot.py --save
```

CI does not gate on `black`, so an unformatted commit is only caught by the
person who wrote it.

## Line endings

LF everywhere, enforced by `.gitattributes` (`* text=auto eol=lf`).
`git ls-files --eol | grep w/crlf` must stay empty.

## CI

Three workflows, one concern each — a release must never be the first time a
config is exercised, and the docs site must be redeployable without a release.

- `test.yml` — every supported Python on Linux plus the platform edges;
  `workflow_dispatch` (with a `ref`), pushes, PRs, and `ci-*` tags.
- `release.yml` (`v*` tag) — test → build → GitHub release → PyPI (Trusted
  Publishing, `skip-existing`). It runs a **non-deploying** docs gate, then
  dispatches `docs.yml` for the tag: a release created with `GITHUB_TOKEN`
  starts no workflow run, so `release: published` alone never deploys.
- `docs.yml` — owns every Pages deploy, self-enables Pages, and is the only
  place that publishes the site.

Throwaway `ci-*` tags are fine to push; delete them afterwards, local and
remote. A `v*` tag is a publish and needs the owner's say-so for that release.

## Releasing

Versions are PEP 440 in `pyproject.toml` and SemVer in tags/changelog — the two
syntaxes differ on purpose. Pre-1.0, **the minor slot means the documented API
broke**; new methods, new optional arguments and fixes are all patches, so a
`~=0.1.0` consumer gets them without re-reading anything.

Bump `version` in the same commit as the `CHANGELOG.md` entry, and keep the
changelog to what changed and what a reader must do about it. The release body
is scraped from the matching `## [x.y.z]` section, so that heading must exist.
