"""Two independent named data clients. Account clients use the same pool interface."""

import asyncio

from pybynix import Bynix, ClientPool


async def main():
    async with ClientPool({"forex": Bynix.public(), "crypto": Bynix.public()}) as pool:
        forex, crypto = await asyncio.gather(
            pool["forex"].get_tick("EUR/USD OTC"), pool["crypto"].get_tick("BTC/USD OTC")
        )
        print(forex.symbol, forex.price)
        print(crypto.symbol, crypto.price)


if __name__ == "__main__":
    asyncio.run(main())
