"""Explicit demo order example. Requires --execute-demo-order; never real funds."""

import argparse
import asyncio
import os

from pybynix import Bynix, Session


async def main(args):
    if not args.execute_demo_order:
        raise SystemExit("Read the source, then pass --execute-demo-order to place one demo order")
    async with Bynix(session=Session(os.environ["BYNIX_TOKEN"])) as client:
        order = await client.place_order(args.symbol, args.direction, args.amount, args.duration)
        print("Order:", order["trade_id"])
        result = await client.wait_order_result(order["trade_id"], timeout=args.duration + 90)
        print("Result:", result["status"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-demo-order", action="store_true")
    parser.add_argument("--symbol", default="EUR/USD OTC")
    parser.add_argument("--direction", choices=["call", "put"], required=True)
    parser.add_argument("--amount", required=True)
    parser.add_argument("--duration", type=int, default=5)
    asyncio.run(main(parser.parse_args()))
