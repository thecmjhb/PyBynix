"""Login, market data and diagnostics."""

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path

from . import (
    BrowserAuth,
    Bynix,
    PasswordAuth,
    Session,
    __version__,
    export_candles,
    load_session,
    save_session,
)
from .domains import check_domains
from .errors import BynixError


async def run(args):
    host = args.host or os.getenv("BYNIX_HOST", "bynixbrokers.com")
    if args.command == "domains":
        print(json.dumps(await check_domains(), indent=2))
        return
    if args.command == "login":
        if args.browser:
            provider = BrowserAuth(profile_dir=args.profile_dir)
        else:
            email = os.getenv("BYNIX_EMAIL") or input("Bynix email: ").strip()
            password = os.getenv("BYNIX_PASSWORD") or getpass.getpass("Bynix password: ")

            async def otp(challenge):
                return getpass.getpass(f"{challenge.method} verification code: ").strip()

            provider = PasswordAuth(email, password, otp_callback=otp)
        async with Bynix(host=host, provider=provider, timeout=args.timeout) as client:
            save_session(client.session, args.session)
            print(f"Session saved to {args.session}. Keep this file private.")
        return
    token = os.getenv("BYNIX_TOKEN")
    session = Session(token, host) if token else None
    if session is None and args.session.is_file():
        session = load_session(args.session)
        if args.host is None:
            host = session.host
    provider = None
    if os.getenv("BYNIX_EMAIL") and os.getenv("BYNIX_PASSWORD"):
        provider = PasswordAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"])
    anonymous = session is None and provider is None
    if anonymous and args.command in {"balances", "trades"}:
        raise ValueError("Run pybynix login first or supply BYNIX_TOKEN")
    async with Bynix(
        session=session,
        provider=provider,
        anonymous=anonymous,
        host=host,
        stream_transport=args.transport,
        timeout=args.timeout,
    ) as client:
        if args.command == "doctor":
            value = {
                "version": __version__,
                "connected": client.connected,
                "mode": "public" if anonymous else "authenticated",
                "websocket": client._socket.connected,
                "platform": await client.get_platform_status(),
                "assets": len(await client.get_assets()),
                "clock": await client.get_server_clock(),
            }
        elif args.command == "assets":
            value = await client.get_assets()
        elif args.command == "payouts":
            value = await client.get_payouts()
        elif args.command == "balances":
            value = await client.get_balance_breakdown()
        elif args.command == "trades":
            value = await client.get_trade_history(account_type=args.account, limit=args.count)
        elif args.command == "candles":
            candles = await client.get_candles(
                args.symbol, count=args.count, interval=args.interval
            )
            if args.output:
                export_candles(candles, args.output)
                print(f"Saved {len(candles)} candles to {args.output}")
                return
            value = [
                {
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ]
        elif args.command == "sentiment":
            value = await client.get_realtime_sentiment(args.symbol)
        elif args.command == "stream":
            async with await client.subscribe_ticks(args.symbol, interval=args.interval) as ticks:
                for _ in range(args.count):
                    tick = await asyncio.wait_for(anext(ticks), args.timeout)
                    print(
                        json.dumps(
                            {
                                "symbol": tick.symbol,
                                "timestamp": tick.timestamp,
                                "price": str(tick.price),
                                "payout": str(tick.payout),
                            }
                        )
                    )
            return
        print(json.dumps(value, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="PyBynix login, market data and diagnostics")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        choices=[
            "login",
            "doctor",
            "domains",
            "assets",
            "payouts",
            "balances",
            "trades",
            "candles",
            "stream",
            "sentiment",
        ],
    )
    parser.add_argument("--host")
    parser.add_argument("--session", type=Path, default=Path.home() / ".pybynix" / "session.json")
    parser.add_argument("--browser", action="store_true", help="Interactive browser login")
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--transport", choices=["auto", "websocket", "polling"], default="auto")
    parser.add_argument("--symbol", default="EUR/USD OTC")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--account", choices=["demo", "real", "funded"], default="demo")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    try:
        asyncio.run(run(args))
    except (BynixError, ValueError, RuntimeError, OSError, TimeoutError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130)
