"""Run without credentials: python examples/public_data.py."""

import asyncio

from pybynix import Bynix


async def main():
    async with Bynix.public() as client:
        print("Assets:", len(await client.get_assets()))
        print("Latest:", await client.get_tick("EUR/USD OTC"))
        async with await client.subscribe_ticks("EUR/USD OTC", interval="15s") as ticks:
            for _ in range(3):
                print(await asyncio.wait_for(anext(ticks), 15))


if __name__ == "__main__":
    asyncio.run(main())
