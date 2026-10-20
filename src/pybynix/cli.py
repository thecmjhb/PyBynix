import argparse
import asyncio
import os

from . import Bynix, PasswordAuth, Session, __version__
from .errors import BynixError


async def run(args):
    token = os.getenv("BYNIX_TOKEN")
    host = os.getenv("BYNIX_HOST", "bynixbrokers.com")
    provider = None
    if not token:
        email, password = os.getenv("BYNIX_EMAIL"), os.getenv("BYNIX_PASSWORD")
        if not email or not password:
            raise ValueError("Set BYNIX_TOKEN or BYNIX_EMAIL and BYNIX_PASSWORD privately")
        provider = PasswordAuth(email, password)
    async with Bynix(
        session=Session(token, host) if token else None, provider=provider, host=host
    ) as client:
        if args.command == "doctor":
            print(
                {
                    "version": __version__,
                    "connected": client.connected,
                    "platform": await client.get_platform_status(),
                    "clock": await client.get_server_clock(),
                }
            )
        elif args.command == "assets":
            for asset in await client.get_assets():
                print(asset.get("trade_symbol", asset.get("name")), asset.get("payout_percentage"))
        elif args.command == "candles":
            for candle in await client.get_candles(args.symbol, count=args.count):
                print(candle)
        elif args.command == "stream":
            count = 0
            async for tick in client.stream_ticks(args.symbol):
                print(tick)
                count += 1
                if count >= args.count:
                    break


def main():
    parser = argparse.ArgumentParser(description="Unofficial Bynix diagnostics; no trade command")
    parser.add_argument("command", choices=["doctor", "assets", "candles", "stream"])
    parser.add_argument("--symbol", default="EUR/USD OTC")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    try:
        asyncio.run(run(args))
    except (BynixError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")
