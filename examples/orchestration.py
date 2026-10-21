"""Callback orchestration with a safe dry-run default.

This example supplies no strategy. Replace ``decide`` with application-owned
logic that returns an explicit OrderIntent when appropriate.
"""

import asyncio

from pybynix import Bynix, ExecutionOrchestrator, OrderIntent


async def decide(event):
    # Application-owned explicit intent. The SDK does not calculate a signal.
    return OrderIntent("EUR/USD OTC", "call", "1.00", 5, client_ref="dry-run-example")


async def sample_events():
    yield {"symbol": "EUR/USD OTC", "price": "sample"}


async def main():
    client = Bynix.public()
    try:
        runner = ExecutionOrchestrator(client, "EUR/USD OTC", decide)
        await runner.run(sample_events())
        print(runner.records[0].status)  # dry_run; no POST was made
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
