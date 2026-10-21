"""Export the last hour of server-time candles without credentials."""

import argparse
import asyncio
from pathlib import Path

from pybynix import Bynix, export_candles


async def main(args):
    async with Bynix.public() as client:
        clock = await client.get_server_clock(args.symbol)
        end = int(clock["server_time_ms"] // 1000)
        candles = await client.get_historical_candles(args.symbol, start=end - 3600, end=end)
        export_candles(candles, args.output)
        print(f"Exported {len(candles)} candles to {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EUR/USD OTC")
    parser.add_argument("--output", type=Path, default=Path("candles.csv"))
    asyncio.run(main(parser.parse_args()))
