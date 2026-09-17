# netboot benchmarks

Micro-benchmarks for the paths netboot runs on every PXE request. **Local
timings are a sanity check, not evidence**: a performance claim in a release or
changelog comes from CI, not from a developer machine, where run-to-run
variance is large.

## Reproduce

```sh
python benchmarks/bench_netboot.py --iterations 20000          # print only
python benchmarks/bench_netboot.py --iterations 20000 --save   # also write results/netboot.json
python benchmarks/bench_netboot.py --json-output /tmp/x.json   # write somewhere else
```

`--targets N` sizes the target table (default 500); the scan metric is
proportional to it, so only compare runs that used the same value.

Results live in `benchmarks/results/` and are **committed**. That is the whole
point: a before/after comparison has to survive in history, which it cannot do
from an ignored directory.

## Metrics

| Metric | What it measures |
| ------ | ---------------- |
| `lookup_target_by_id` | `Pixie.lookup_target` for a query that *is* a target id — answered by the id index, no scan. |
| `lookup_target_scan_by_ip` | `Pixie.lookup_target` for the last target's IP. Not an id, so it scans the whole table: the worst case. |
| `template_names` | `PixieContext._template_names`, the candidate-name list rebuilt for every render. |

Each metric reports ms-per-call as `min`/`median`/`max` over 7 repeats of
`--iterations` calls. **Compare on the median** — a single average hides
run-to-run noise.

## Schema (`results/netboot.json`)

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

`results/netboot.json` holds the current baseline, recorded on Windows ARM64
(CPython 3.14, an emulated x64 build) at `--iterations 5000`. It is the first
entry taken after the metric set was corrected: the metric previously called
`lookup_target_last` claimed to measure a full scan but passed a query that is
a target id, so the id index answered it without scanning. The replacement pair
shows the difference the old metric hid — about 0.001 ms for the indexed hit
against 0.088 ms for a 500-target scan on the same machine.
