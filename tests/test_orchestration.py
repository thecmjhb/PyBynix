import asyncio

import pytest

from pybynix import ExecutionOrchestrator, OrderIntent
from pybynix.errors import OrderOutcomeUnknown


async def events(*values):
    for value in values:
        yield value


class FakeClient:
    def __init__(self):
        self.calls = []
        self.settlements = []

    async def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"trade_id": f"trade-{len(self.calls)}", "client_ref": kwargs["client_ref"]}

    async def wait_order_result(self, trade_id, **kwargs):
        self.settlements.append((trade_id, kwargs))
        return {"trade_id": trade_id, "status": "won"}

class BlockingSubscription:
    def __init__(self):
        self.closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.closed.wait()
        raise StopAsyncIteration


class StreamClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.subscription: BlockingSubscription | None = None

    async def subscribe_ticks(self, symbol, *, interval):
        return self.subscription


def intent(ref="one", account_type="demo"):
    return OrderIntent("EUR/USD OTC", "call", "1.00", 5, account_type, ref)


async def test_dry_run_is_default_and_callback_can_be_async():
    client = FakeClient()

    async def decide(event):
        return intent(event)

    runner = ExecutionOrchestrator(client, "EUR/USD OTC", decide)
    await runner.run(events("first"))

    assert client.calls == []
    assert runner.records[0].status == "dry_run"


async def test_explicit_demo_execution_settles_without_retry():
    client = FakeClient()
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: intent(event),
        dry_run=False,
        cooldown=0,
    )

    await runner.run(events("first"))

    assert len(client.calls) == 1
    assert runner.records[0].status == "settled"
    assert client.settlements[0][0] == "trade-1"
    assert runner.active_orders == 0


async def test_cooldown_and_max_concurrency_are_explicit_guards():
    client = FakeClient()
    release = asyncio.Event()

    async def wait_for_settlement(trade_id, **kwargs):
        await release.wait()
        return {"trade_id": trade_id, "status": "won"}

    client.wait_order_result = wait_for_settlement
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: intent(event),
        dry_run=False,
        max_concurrent_orders=1,
        cooldown=60,
    )

    first = await runner.submit(intent("first"))
    second = await runner.submit(intent("second"))
    assert first.status == "accepted"
    assert second.status == "skipped_cooldown"
    assert len(client.calls) == 1
    release.set()
    await runner.stop()


async def test_uncertain_submission_is_recorded_and_never_retried():
    client = FakeClient()

    async def uncertain(**kwargs):
        raise OrderOutcomeUnknown(kwargs["client_ref"])

    client.place_order = uncertain
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: intent(event),
        dry_run=False,
    )

    record = await runner.submit(intent("uncertain"))
    assert record.status == "unknown"
    assert runner.active_orders == 0


async def test_demo_guard_rejects_non_demo_intents():
    runner = ExecutionOrchestrator(FakeClient(), "EUR/USD OTC", lambda event: None, dry_run=False)
    with pytest.raises(ValueError, match="demo_only"):
        await runner.submit(intent("real", account_type="real"))


async def test_concurrent_limit_and_graceful_start_stop():
    client = StreamClient()
    release = asyncio.Event()
    subscription = BlockingSubscription()
    client.subscription = subscription

    async def wait_for_settlement(trade_id, **kwargs):
        await release.wait()
        return {"trade_id": trade_id, "status": "won"}

    client.wait_order_result = wait_for_settlement
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: None,
        dry_run=False,
        max_concurrent_orders=1,
    )
    await runner.start()
    await asyncio.sleep(0)
    assert runner.running

    first, second = await asyncio.gather(runner.submit(intent("first")), runner.submit(intent("second")))
    assert first.status == "accepted"
    assert second.status == "skipped_concurrency"
    release.set()
    await asyncio.wait_for(runner.stop(), 3)
    assert not runner.running
