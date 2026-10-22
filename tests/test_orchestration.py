import asyncio

import pytest

from pybynix import ExecutionOrchestrator, OrderIntent
from pybynix.errors import OrderJournalError, OrderOutcomeUnknown


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


class IdleExternalSource:
    """An external source without aclose(), used to prove stop cancellation."""

    def __init__(self):
        self.released = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.released.wait()
        raise StopAsyncIteration


class OneThenIdleSource:
    def __init__(self):
        self.released = asyncio.Event()
        self.emitted = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.emitted:
            self.emitted = True
            return "tick"
        await self.released.wait()
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
        client.calls.append(kwargs)
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
    assert runner.active_orders == 1
    assert runner.safety_paused
    assert runner.pause_reason == "unknown_order_outcome"
    blocked = await runner.submit(intent("after-uncertain"))
    assert blocked.status == "skipped_safety_pause"
    assert len(client.calls) == 1


async def test_journal_failure_preserves_receipt_and_pauses_new_submissions():
    client = FakeClient()
    receipt = {"trade_id": "accepted-1", "client_ref": "journal-failure"}

    async def journal_failure(**kwargs):
        raise OrderJournalError(receipt)

    client.place_order = journal_failure
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: intent(event),
        dry_run=False,
        settle=False,
    )

    record = await runner.submit(intent("journal-failure"))
    assert record.status == "accepted_journal_error"
    assert record.receipt == receipt
    assert runner.state == "paused"
    assert runner.pause_reason == "order_journal_error"
    assert (await runner.submit(intent("blocked"))).status == "skipped_safety_pause"
    assert runner.active_orders == 1


async def test_settlement_timeout_keeps_capacity_reserved_and_pauses():
    client = FakeClient()

    async def timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    client.wait_order_result = timeout
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: intent(event),
        dry_run=False,
        settlement_timeout=0.05,
        settlement_poll_interval=0.01,
    )

    record = await runner.submit(intent("settlement-timeout"))
    await asyncio.wait_for(runner._wait_settlements(), 1)
    assert record.status == "settlement_unknown"
    assert runner.state == "paused"
    assert runner.pause_reason == "settlement_timeout"
    assert runner.active_orders == 1
    assert (await runner.submit(intent("blocked"))).status == "skipped_safety_pause"


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


async def test_stop_cancels_idle_external_source_within_timeout():
    source = OneThenIdleSource()
    runner = ExecutionOrchestrator(FakeClient(), "EUR/USD OTC", lambda event: None)
    await runner.start(source)
    await asyncio.wait_for(runner._run_ready.wait(), 1)
    await asyncio.wait_for(runner.stop(), 1)
    assert runner.state == "stopped"
    assert not runner.running


async def test_stop_does_not_cancel_in_flight_submission_or_allow_late_intent():
    client = FakeClient()
    started = asyncio.Event()
    release = asyncio.Event()

    async def place_order(**kwargs):
        client.calls.append(kwargs)
        started.set()
        await release.wait()
        return {"trade_id": "trade-1", "client_ref": kwargs["client_ref"]}

    client.place_order = place_order
    source = OneThenIdleSource()
    runner = ExecutionOrchestrator(
        client,
        "EUR/USD OTC",
        lambda event: intent("first"),
        dry_run=False,
    )
    await runner.start(source)
    await asyncio.wait_for(started.wait(), 1)
    stop_task = asyncio.create_task(runner.stop())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(stop_task, 1)
    assert len(client.calls) == 1
    with pytest.raises(RuntimeError, match="stopped"):
        await runner.submit(intent("late"))


async def test_records_and_errors_are_bounded():
    runner = ExecutionOrchestrator(
        FakeClient(),
        "EUR/USD OTC",
        lambda event: None,
        max_records=2,
        max_errors=2,
    )
    for ref in ("one", "two", "three"):
        await runner.submit(intent(ref))
    runner._record_error(ValueError("one"))
    runner._record_error(ValueError("two"))
    runner._record_error(ValueError("three"))
    assert [record.intent.client_ref for record in runner.records] == ["two", "three"]
    assert [str(error) for error in runner.errors] == ["two", "three"]
