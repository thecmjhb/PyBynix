"""Read-only continuous market/account observation with a finite duration."""

import argparse
import asyncio
import hashlib
import json
import os
import time
import tracemalloc
from pathlib import Path

from pybynix import Bynix, PasswordAuth, __version__, load_session


def source_fingerprint():
    root = Path(__file__).resolve().parents[1] / "src" / "pybynix"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.py"))}


async def run(args):
    session = load_session(args.session)
    provider = None
    if os.getenv("BYNIX_EMAIL") and os.getenv("BYNIX_PASSWORD"):
        provider = PasswordAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"])
    report = {
        "version": __version__,
        "started_at_unix": time.time(),
        "source_start": source_fingerprint(),
        "symbols": {},
        "checkpoints": [],
        "forced_reconnects": 0,
    }
    tracemalloc.start()
    started = time.monotonic()
    cpu = time.process_time()
    client = Bynix(session=session, host=session.host, provider=provider, queue_size=64)
    async with client:

        async def consume(symbol, interval):
            row = {
                "ticks": 0,
                "closed_candles": 0,
                "max_arrival_gap_seconds": 0,
                "last_timestamp_ms": 0,
            }
            report["symbols"][symbol + ":" + interval] = row
            last = None
            async with await client.subscribe_ticks(symbol, interval=interval) as ticks:
                async for tick in ticks:
                    now = time.monotonic()
                    if last is not None:
                        row["max_arrival_gap_seconds"] = max(
                            row["max_arrival_gap_seconds"], now - last
                        )
                    last = now
                    row["ticks"] += 1
                    row["closed_candles"] += tick.closed is not None
                    row["last_timestamp_ms"] = tick.timestamp

        jobs = [
            asyncio.create_task(consume(s, i))
            for s, i in [("EUR/USD OTC", "15s"), ("GBP/USD OTC", "1m"), ("BTC/USD OTC", "5s")]
        ]

        async def checkpoint():
            while True:
                await asyncio.sleep(30)
                elapsed = time.monotonic() - started
                current, peak = tracemalloc.get_traced_memory()
                report["checkpoints"].append(
                    {
                        "elapsed_s": elapsed,
                        "python_heap_bytes": current,
                        "python_heap_peak_bytes": peak,
                        "metrics": dict(client.metrics),
                    }
                )
                report["elapsed_seconds"] = elapsed
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(report, indent=2))
                failed = [j for j in jobs if j.done() and j.exception() is not None]
                if failed:
                    raise failed[0].exception()
                if int(elapsed // 300) > report["forced_reconnects"]:
                    await client.reconnect()
                    report["forced_reconnects"] += 1
                print(
                    "soak_progress",
                    round(elapsed),
                    sum(r["ticks"] for r in report["symbols"].values()),
                    flush=True,
                )

        observer = asyncio.create_task(checkpoint())
        try:
            sleeper = asyncio.create_task(asyncio.sleep(args.duration))
            done, _ = await asyncio.wait(
                [sleeper, observer, *jobs], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            observer.cancel()
            sleeper.cancel()
            for job in jobs:
                job.cancel()
            await asyncio.gather(observer, sleeper, *jobs, return_exceptions=True)
        report["metrics"] = dict(client.metrics)
    report.update(
        elapsed_seconds=time.monotonic() - started,
        process_cpu_seconds=time.process_time() - cpu,
        finished_at_unix=time.time(),
        source_end=source_fingerprint(),
    )
    report["source_unchanged"] = report["source_start"] == report["source_end"]
    report["completed"] = report["elapsed_seconds"] >= args.duration
    args.out.write_text(json.dumps(report, indent=2))
    print("soak_complete", report["completed"], report["elapsed_seconds"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=3600)
    args = parser.parse_args()
    if args.duration < 60:
        parser.error("duration must be at least sixty seconds")
    asyncio.run(run(args))
