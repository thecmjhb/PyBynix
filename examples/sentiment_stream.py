"""Observe the platform-style derived display model; no trading decisions."""

import asyncio

from pybynix import Bynix


async def main():
    async with Bynix.public() as client:
        print(await client.get_realtime_sentiment("EUR/USD OTC"))
        async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
            for _ in range(5):
                await asyncio.wait_for(anext(ticks), 15)
                print(await client.get_realtime_sentiment("EUR/USD OTC"))


if __name__ == "__main__":
    asyncio.run(main())
