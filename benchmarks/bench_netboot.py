"""netboot micro-benchmarks: the per-request hot paths.

Measures the two operations netboot runs most often per PXE request:

* ``lookup_target`` — a linear scan over the configured targets matching by
  hostname prefix / MAC / IP;
* ``_template_names`` — the candidate template-name list built for every render.

Run:

    python benchmarks/bench_netboot.py --iterations 20000
    python benchmarks/bench_netboot.py --json-output results/netboot.json

Each metric reports min/median/max ms-per-call over the sample; compare on the
median (a single average hides run-to-run noise). Local timings are a sanity
check only — a release perf claim comes from CI.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import statistics
import sys
import timeit
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, (REPO_ROOT / "src").as_posix())

import netboot  # noqa: E402


def _build_netboot(n_targets: int) -> "netboot.Pixie":
    targets = {}
    for i in range(n_targets):
        targets[f"host{i}"] = {
            "hostname": f"host{i}",
            "ip": f"10.0.{i // 256}.{i % 256}",
            "image": "debian",
        }
    return netboot.Pixie(
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/16"}},
        targets=targets,
    )


def _sample(fn, iterations: int, repeats: int = 7) -> "dict[str, float]":
    # ms per call, one timing per repeat; report min/median/max across repeats.
    per_call = [
        (timeit.timeit(fn, number=iterations) / iterations) * 1000.0
        for _ in range(repeats)
    ]
    return {
        "min_ms": min(per_call),
        "median_ms": statistics.median(per_call),
        "max_ms": max(per_call),
    }


def run_benchmarks(iterations: int, n_targets: int = 500) -> "dict[str, dict]":
    p = _build_netboot(n_targets)
    # Worst-case lookup: the last target, forcing a full scan.
    last = f"host{n_targets - 1}"
    ctx = p.make_context(p.lookup_target("host0"))

    return {
        "lookup_target_last": _sample(lambda: p.lookup_target(last), iterations),
        "template_names": _sample(
            lambda: ctx._template_names("boot.j2"), iterations
        ),
    }


def _report(iterations: int, results: "dict[str, dict]") -> dict:
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "iterations": iterations,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "netboot_version": netboot.Pixie.VERSION,
        "metrics": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run netboot micro-benchmarks")
    parser.add_argument("--iterations", type=int, default=20000)
    parser.add_argument("--targets", type=int, default=500)
    parser.add_argument(
        "--json-output",
        type=pathlib.Path,
        help="Optional path to write the structured JSON report",
    )
    args = parser.parse_args()

    results = run_benchmarks(args.iterations, args.targets)
    report = _report(args.iterations, results)

    for name, m in results.items():
        print(
            f"{name:24s} median={m['median_ms']:.6f} ms  "
            f"(min={m['min_ms']:.6f} max={m['max_ms']:.6f})"
        )

    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_output}")


if __name__ == "__main__":
    main()
