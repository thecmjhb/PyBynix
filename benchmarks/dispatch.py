"""Offline typed event fan-out benchmark; not a venue/network execution benchmark."""

import argparse
import asyncio
import json
import platform
import statistics
import time
from pathlib import Path

from pybynix import Bynix, __version__


async def run(args):
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/tick.json"
    tick = json.loads(fixture.read_text())
    client = Bynix.public(queue_size=2, max_requests_per_second=None)
    client._ready.set()
    subscriptions = [await client.subscribe_ticks(tick["symbol"]) for _ in range(args.consumers)]
    samples = []
    timestamp = tick["timestamp"]
    try:
        for batch in range(args.batches + 1):
            start = time.perf_counter_ns()
            for _ in range(args.iterations):
                timestamp += 1
                event = {**tick, "timestamp": timestamp, "server_time": timestamp}
                await client._on_tick(event)
                for subscriber in subscriptions:
                    await anext(subscriber)
            elapsed = time.perf_counter_ns() - start
            if batch:
                samples.append(elapsed / args.iterations)
    finally:
        for subscriber in subscriptions:
            await subscriber.aclose()
        await client.close()
    report = {
        "benchmark": "typed_tick_fanout_and_consumer_drain",
        "version": __version__,
        "python": platform.python_version(),
        "system": platform.system(),
        "consumers": args.consumers,
        "batches": args.batches,
        "iterations_per_batch": args.iterations,
        "warmup_iterations": args.iterations,
        "batch_mean_ns_per_event": samples,
        "median_batch_mean_ns_per_event": statistics.median(samples),
        "limitations": "One public-market fixture with distinct synthetic timestamps; excludes socket delivery and trading.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--consumers", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--batches", type=int, default=10)
    args = parser.parse_args()
    if min(args.consumers, args.iterations, args.batches) < 1:
        parser.error("positive dimensions required")
    asyncio.run(run(args))
