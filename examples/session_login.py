"""Read a CLI-created session and print a balance without exporting the token."""

import argparse
import asyncio
from pathlib import Path

from pybynix import Bynix


async def main(args):
    async with Bynix.from_session_file(args.session) as client:
        print(await client.get_balance_breakdown())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, default=Path.home() / ".pybynix" / "session.json")
    asyncio.run(main(parser.parse_args()))
