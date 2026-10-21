"""Reconcile an existing journal reference; never submits a replacement order."""

import argparse
import asyncio
from pathlib import Path

from pybynix import Bynix, SQLiteJournal


async def main(args):
    async with Bynix.from_session_file(args.session, journal=SQLiteJournal(args.journal)) as client:
        result = await client.reconcile_order(client_ref=args.client_ref)
        if result:
            print(result.get("trade_id"), result.get("status"))
        else:
            print("Unresolved. Preserve this reference and do not automatically resend.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--client-ref", required=True)
    asyncio.run(main(parser.parse_args()))
