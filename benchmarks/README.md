# netboot benchmarks

Micro-benchmarks for the paths netboot runs on every PXE request. **Local
timings are a sanity check, not evidence**: a performance claim in a release or
changelog comes from CI, not from a developer machine, where run-to-run
variance is large.

## Reproduce

```sh
python benchmarks/bench_netboot.py --iterations 20000          # print only
python benchmarks/bench_netboot.py --iterations 20000 --save   # also write results/<version>-py<X.Y>.json
python benchmarks/bench_netboot.py --json-output /tmp/x.json   # write somewhere else
```

`--targets N` sizes the target table (default 500); the scan metric is
proportional to it, so only compare runs that used the same value.

Results live in `benchmarks/results/`, one file per netboot version and
Python minor (`0.4.0-py3.14.json`), and are **committed**: the trend across
releases has to survive in history, which it cannot do from an ignored
directory or a file each run overwrites.

## Metrics

| Metric | What it measures |
| ------ | ---------------- |
| `lookup_target_by_id` | `Pixie.lookup_target` for a query that *is* a target id — answered by the id index, no scan. |
| `lookup_target_scan_by_ip` | `Pixie.lookup_target` for the last target's IP. Not an id, so it scans the whole table: the worst case. |
| `template_names` | `PixieContext._template_names`, the candidate-name list rebuilt for every render. |
| `make_context` | `Pixie.make_context` for one target: image, zone, globals and a renderer. |
| `render` | `PixieContext.render("boot.cfg")` of a two-line Jinja file. These two run a tenth of `--iterations`. |

Each metric reports ms-per-call as `min`/`median`/`max` over 7 repeats of
`--iterations` calls. **Compare on the median** — a single average hides
run-to-run noise.

## Schema (`results/<version>-py<X.Y>.json`)

```json
{
  "generated": "<ISO-8601 UTC>",
  "iterations": 20000,
  "python": "3.14.6",
  "platform": "<platform.platform()>",
  "netboot_version": "0.1.3",
  "metrics": {
    "<metric>": { "min_ms": 0.0, "median_ms": 0.0, "max_ms": 0.0 }
  }
}
```

Compare two reports with
`py -3 $ENGINEERING_OVERLAY_ROOT/tools/compare_bench.py <old.json> <new.json>`.

## Baseline

`results/0.1.3-py3.14.json` is the baseline, recorded on Windows ARM64
(CPython 3.14, an emulated x64 build) at `--iterations 5000`; `make_context`
and `render` start at `0.4.0-py3.14.json`. Compare a new run against it with
`compare_bench.py`, on the same machine and Python, and treat the result as a
sanity check.
