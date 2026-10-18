# netboot — working in this checkout

| Shipped header | Covers |
| --- | --- |
| [`src/netboot/AGENTS.md`](src/netboot/AGENTS.md) | the public API and the `pixie` command line; it ships in the wheel |

Read the header to *use* netboot, this file to *work on* it.

## Layout

| Path | What |
| ---- | ---- |
| `src/netboot/` | the package: `engine.py` the engine, `dhcp.py` what a target is served, `main.py` the CLI, `exceptions.py` the errors |
| `src/netboot/cli/` | the `pixie` command line: `_root.py` (globals, discovery, dispatch), `_config.py` (loading `pixie.yaml`), one class per command (`initiate`, `complete`, `render`, `dhcp-config`); `main.py` re-exports it |
| `tests/` | pytest suite; nothing here touches the network |
| `benchmarks/` | micro-benchmarks + committed results ([README](benchmarks/README.md)) |
| `examples/` | a runnable config, templates and a DHCP plugin ([README](examples/README.md)) |
| `docs/` + `mkdocs.yml` | the documentation site |

## Environment

Per-version venvs, gitignored: `.venv/<version>-<os>-<arch>/`. Develop on the
latest Python and also run the floor from `requires-python` (3.9) before
finishing a chunk of work.

```sh
python -m venv .venv/3.14-nt-amd64
.venv/3.14-nt-amd64/Scripts/python -m pip install -e ".[dev,docs,config]"
```

`dev` carries every library a test needs — the DHCP backends' (so
`tests/test_dhcp_golden.py` runs) and the optional template engines' (`mako`,
`python-liquid`, `pybars3`, `chevron`) — so nothing skips for a missing
package; `pytest -rs` is how you see what does.

Templates are rendered by [`tplx`](https://github.com/jose-pr/tplx), which has
its own engine tests; `tests/test_templates.py` pins what netboot hands it. Its
`erb` and `epp` cases need **`ruby`** and **`puppet`** installed and skip
without them, so a change to what those engines receive is unverified until the
suite has run where both exist. To work against an unreleased tplx, install a
checkout over the released one: `pip install --no-deps -e ../tplx`.

## Checks

```sh
<venv>/python -m pytest -q -rs                 # the suite (skips are visible)
<venv>/python -m black src tests benchmarks    # formatter; run before committing
<venv>/python -m mkdocs build --strict         # docs must build clean
<venv>/python -m build                         # sdist + wheel
<venv>/python benchmarks/bench_netboot.py --save
```

CI does not gate on `black`, so an unformatted commit is only caught by the
person who wrote it.

## Conventions

**Names** are deliberate, not drift: **`netboot`** is the library (distribution, import
package, `python -m netboot`), **`pixie`** is the CLI identity (console script,
prog name, `PIXIE_*` env vars, `pixie.yaml`), and **`Pixie*`** is the public
class prefix (`Pixie`, `PixieTarget`, `PixieContext`, `PixieEvent`). PXE is
pronounced "pixie". `netboot` remains the conventional variable name for an
engine instance, which is why plugin command modules take `run(netboot, args, conf)`.

**Line endings**: LF everywhere, enforced by `.gitattributes` (`* text=auto eol=lf`).
`git ls-files --eol | grep w/crlf` must stay empty.

## Releasing

Versions are PEP 440 in `pyproject.toml` and SemVer in tags/changelog — the two
syntaxes differ on purpose. Pre-1.0, **the minor slot means the documented API
broke**; new methods, new optional arguments and fixes are all patches, so a
`~=0.1.0` consumer gets them without re-reading anything.

Bump `version` in the same commit as the `CHANGELOG.md` entry, and keep the
changelog to what changed and what a reader must do about it. The release body
is scraped from the matching `## [x.y.z]` section, so that heading must exist.

Three workflows, one concern each — a release must never be the first time a
config is exercised, and the docs site must be redeployable without a release.

- `test.yml` — every supported Python on Linux plus the platform edges;
  `workflow_dispatch` (with a `ref`), pushes, PRs, and `ci-*` tags.
- `test.yml` also runs the suite at every declared dependency floor (3.9),
  and installs the built wheel from a directory where the checkout is not
  importable.
- `release.yml` (`v*` tag) — tests on three OSes and at the floors → build
  (the tag must name the built version; `twine check`) → the installed wheel's
  `pixie` runs `examples/` on three OSes, beside a **non-deploying** strict
  docs gate → GitHub release → PyPI (Trusted Publishing, `skip-existing`, final
  tags only) → dispatch `docs.yml` at the tag: a release created with
  `GITHUB_TOKEN` starts no workflow run, so nothing can listen for it.
- `docs.yml` — owns every Pages deploy, self-enables Pages, and is the only
  place that publishes the site.

Throwaway `ci-*` tags are fine to push; delete them afterwards, local and
remote. A `v*` tag is a publish and needs the owner's say-so for that release.
