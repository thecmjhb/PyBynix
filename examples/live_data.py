"""Read-only example: BYNIX_TOKEN or BYNIX_EMAIL + BYNIX_PASSWORD."""

import asyncio
import os

from pybynix import Bynix, PasswordAuth, Session


async def main():
    token = os.getenv("BYNIX_TOKEN")
    async with Bynix(
        session=Session(token) if token else None,
        provider=PasswordAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"])
        if not token and os.getenv("BYNIX_EMAIL") and os.getenv("BYNIX_PASSWORD")
        else None,
        anonymous=not token and not (os.getenv("BYNIX_EMAIL") and os.getenv("BYNIX_PASSWORD")),
    ) as client:
        print("Assets:", len(await client.get_assets()))
        print("Candles:", await client.get_candles("EUR/USD OTC", count=5))
        async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
            for _ in range(10):
                print(await anext(ticks))


if __name__ == "__main__":
    asyncio.run(main())
