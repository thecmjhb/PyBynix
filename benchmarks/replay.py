"""Offline microbenchmark. Does not measure order execution or network latency."""

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

from pybynix import Tick, __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--batches", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1 or args.batches < 2:
        parser.error("iterations must be positive and batches at least two")
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tick.json"
    raw = fixture.read_text()
    for _ in range(1000):
        Tick.from_wire(json.loads(raw))
    samples = []
    for _ in range(args.batches):
        start = time.perf_counter_ns()
        for _ in range(args.iterations):
            Tick.from_wire(json.loads(raw))
        samples.append((time.perf_counter_ns() - start) / args.iterations)
    report = {
        "benchmark": "json_decode_plus_decimal_tick_model",
        "version": __version__,
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "iterations_per_batch": args.iterations,
        "batches": args.batches,
        "warmup": 1000,
        "fixture": "tests/fixtures/tick.json",
        "batch_mean_ns_per_event": samples,
        "median_batch_mean_ns_per_event": statistics.median(samples),
        "limitations": "One recorded market tick; no network, queue dispatch, trading or competing SDK comparison.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
