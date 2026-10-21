"""Safe orchestration for caller-supplied order intents.

This module deliberately contains no trading strategy, signal generation, sizing
model, or retry policy.  A caller supplies a callback that turns a market event
into an :class:`OrderIntent` (or ``None``); this layer only applies execution
guards and delegates the actual journal-aware submission to :class:`Bynix`.
"""

import asyncio
import inspect
import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterable, Awaitable, Callable, Iterable

from .errors import OrderJournalError, OrderOutcomeUnknown
from .models import AccountType

DecisionCallback = Callable[[Any], "OrderIntent | Iterable[OrderIntent] | Awaitable[OrderIntent | Iterable[OrderIntent] | None] | None"]


@dataclass(frozen=True)
class OrderIntent:
    """An explicit order request produced by application code.

    ``OrderIntent`` is only a transport object.  It does not calculate a
    direction, amount, duration, or signal.  The caller must provide all of
    those fields explicitly.
    """

    symbol: str
    direction: str
    amount: Any
    duration: int
    account_type: AccountType | str = AccountType.DEMO
    client_ref: str | None = None
    challenge_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("OrderIntent.symbol must be a nonempty string")
        if self.direction not in {"call", "put"}:
            raise ValueError("OrderIntent.direction must be call or put")
        try:
            money = Decimal(str(self.amount))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("OrderIntent.amount must be a positive amount") from None
        try:
            valid_money = money.is_finite() and money > 0 and money == money.quantize(Decimal("0.01"))
        except InvalidOperation:
            valid_money = False
        if not valid_money:
            raise ValueError("OrderIntent.amount must be positive with at most two decimals")
        if isinstance(self.duration, bool) or not isinstance(self.duration, int) or self.duration < 5:
            raise ValueError("OrderIntent.duration must be an integer of at least 5 seconds")
        object.__setattr__(self, "account_type", AccountType(self.account_type))
        if self.client_ref is not None and (
            not isinstance(self.client_ref, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.client_ref)
        ):
            raise ValueError("OrderIntent.client_ref must contain 1..128 safe identifier characters")

    def as_place_order_kwargs(self) -> dict[str, Any]:
        """Return arguments suitable for ``Bynix.place_order``."""

        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "amount": self.amount,
            "duration": self.duration,
            "account_type": self.account_type,
            "client_ref": self.client_ref,
            "challenge_id": self.challenge_id,
        }


ExecutionIntent = OrderIntent


@dataclass
class ExecutionRecord:
    """Local lifecycle record for one accepted, skipped, or failed intent."""

    intent: OrderIntent
    status: str
    receipt: dict[str, Any] | None = None
    settlement: dict[str, Any] | None = None
    error: Exception | None = field(default=None, repr=False)
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class ExecutionOrchestrator:
    """Run a caller-owned decision callback over a tick stream.

    The default is ``dry_run=True`` and ``demo_only=True``.  Turning off dry
    run still permits only demo intents unless the caller explicitly opts out
    of the demo guard with ``demo_only=False``; non-demo execution remains
    subject to the connected client's own ``allow_real_trading`` checks.
    """

    def __init__(
        self,
        client,
        symbol: str,
        decision_callback: DecisionCallback,
        *,
        interval: str = "1m",
        dry_run: bool = True,
        demo_only: bool = True,
        cooldown: float = 0.0,
        max_concurrent_orders: int = 1,
        settle: bool = True,
        settlement_timeout: float = 120.0,
        settlement_poll_interval: float = 2.0,
    ):
        if not callable(decision_callback):
            raise TypeError("decision_callback must be callable")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a nonempty string")
        if not isinstance(interval, str) or not interval.strip():
            raise ValueError("interval must be a nonempty string")
        if not isinstance(dry_run, bool) or not isinstance(demo_only, bool) or not isinstance(settle, bool):
            raise TypeError("dry_run, demo_only and settle must be booleans")
        if not isinstance(cooldown, (int, float)) or isinstance(cooldown, bool) or not math.isfinite(cooldown) or cooldown < 0:
            raise ValueError("cooldown must be finite and nonnegative")
        if not isinstance(max_concurrent_orders, int) or isinstance(max_concurrent_orders, bool) or max_concurrent_orders < 1:
            raise ValueError("max_concurrent_orders must be a positive integer")
        if not isinstance(settlement_timeout, (int, float)) or not math.isfinite(settlement_timeout) or settlement_timeout <= 0:
            raise ValueError("settlement_timeout must be finite and positive")
        if not isinstance(settlement_poll_interval, (int, float)) or not math.isfinite(settlement_poll_interval) or settlement_poll_interval <= 0:
            raise ValueError("settlement_poll_interval must be finite and positive")
        self.client = client
        self.symbol = symbol
        self.interval = interval
        self.decision_callback = decision_callback
        self.dry_run = dry_run
        self.demo_only = demo_only
        self.cooldown = float(cooldown)
        self.max_concurrent_orders = max_concurrent_orders
        self.settle = settle
        self.settlement_timeout = float(settlement_timeout)
        self.settlement_poll_interval = float(settlement_poll_interval)
        self.records: list[ExecutionRecord] = []
        self.errors: list[Exception] = []
        self._settlement_tasks: set[asyncio.Task] = set()
        self._state_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None
        self._run_ready = asyncio.Event()
        self._subscription = None
        self._stop_event = asyncio.Event()
        self._running = False
        self._stopped = False
        self._active_orders = 0
        self._last_submission_at: float | None = None

    @property
    def active_orders(self) -> int:
        """Number of accepted orders whose settlement task is still active."""

        return self._active_orders

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, events: AsyncIterable[Any] | None = None):
        """Start background consumption and return this orchestrator."""

        if self._run_task is not None and not self._run_task.done():
            raise RuntimeError("ExecutionOrchestrator is already running")
        if self._stopped:
            raise RuntimeError("ExecutionOrchestrator cannot be restarted after stop")
        self._stop_event.clear()
        self._run_ready.clear()
        self._run_task = asyncio.create_task(self.run(events))
        return self

    async def run(self, events: AsyncIterable[Any] | None = None):
        """Consume events until the source ends or :meth:`stop` is called.

        Pass an async iterable in tests or applications with their own event
        source.  When omitted, the client's bounded tick subscription is used.
        """

        if self._running:
            raise RuntimeError("ExecutionOrchestrator is already running")
        self._running = True
        self._run_ready.set()
        try:
            if events is None:
                self._subscription = await self.client.subscribe_ticks(self.symbol, interval=self.interval)
                if self._stop_event.is_set():
                    await self._subscription.aclose()
                    return
                async with self._subscription as source:
                    await self._consume(source)
            else:
                await self._consume(events)
        finally:
            self._subscription = None
            self._running = False
            await self._wait_settlements()

    async def stop(self):
        """Stop intake, close the stream, and drain settlement tracking."""

        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        if self._run_task is not None and not self._run_task.done():
            await self._run_ready.wait()
        if self._subscription is not None:
            await self._subscription.aclose()
        current = asyncio.current_task()
        if self._run_task is not None and self._run_task is not current:
            await self._run_task
        await self._wait_settlements()

    async def submit(self, intent: OrderIntent) -> ExecutionRecord:
        """Submit one explicit intent, subject to all orchestration guards."""

        if not isinstance(intent, OrderIntent):
            raise TypeError("submit expects an OrderIntent")
        if self._stopped:
            raise RuntimeError("ExecutionOrchestrator has been stopped")
        if self.demo_only and intent.account_type is not AccountType.DEMO:
            raise ValueError("demo_only=True accepts only demo OrderIntent values")
        now = time.monotonic()
        async with self._state_lock:
            if self._last_submission_at is not None and now - self._last_submission_at < self.cooldown:
                record = ExecutionRecord(intent, "skipped_cooldown", completed_at=time.time())
                self.records.append(record)
                return record
            if not self.dry_run and self._active_orders >= self.max_concurrent_orders:
                record = ExecutionRecord(intent, "skipped_concurrency", completed_at=time.time())
                self.records.append(record)
                return record
            self._last_submission_at = now
            if not self.dry_run:
                self._active_orders += 1
            record = ExecutionRecord(intent, "dry_run" if self.dry_run else "submitting")
            self.records.append(record)

        if self.dry_run:
            record.completed_at = time.time()
            return record

        try:
            receipt = await self.client.place_order(**intent.as_place_order_kwargs())
        except OrderOutcomeUnknown as error:
            record.status = "unknown"
            record.error = error
            record.completed_at = time.time()
            await self._release_active()
            return record
        except OrderJournalError as error:
            record.status = "accepted_journal_error"
            record.error = error
            record.receipt = error.order
            record.completed_at = time.time()
            trade_id = record.receipt.get("trade_id") if isinstance(record.receipt, dict) else None
            if trade_id and self.settle:
                self._track_settlement(record, trade_id)
            else:
                await self._release_active()
            return record
        except Exception as error:
            record.status = "rejected"
            record.error = error
            record.completed_at = time.time()
            await self._release_active()
            return record

        record.receipt = receipt
        record.status = "accepted"
        record.completed_at = time.time()
        trade_id = receipt.get("trade_id") if isinstance(receipt, dict) else None
        if trade_id and self.settle:
            self._track_settlement(record, trade_id)
        else:
            await self._release_active()
        return record

    async def _consume(self, events):
        async for event in events:
            if self._stop_event.is_set():
                break
            try:
                decision = self.decision_callback(event)
                if inspect.isawaitable(decision):
                    decision = await decision
                if decision is None:
                    continue
                if isinstance(decision, OrderIntent):
                    decisions = (decision,)
                else:
                    if isinstance(decision, (str, bytes, dict)):
                        raise TypeError("decision_callback must return OrderIntent, iterable of intents, or None")
                    decisions = decision
                for intent in decisions:
                    await self.submit(intent)
            except Exception as error:
                self.errors.append(error)

    def _track_settlement(self, record: ExecutionRecord, trade_id: str):
        task = asyncio.create_task(self._settle(record, trade_id))
        self._settlement_tasks.add(task)
        task.add_done_callback(self._settlement_tasks.discard)

    async def _settle(self, record: ExecutionRecord, trade_id: str):
        try:
            record.settlement = await self.client.wait_order_result(
                trade_id,
                account_type=record.intent.account_type,
                timeout=self.settlement_timeout,
                poll_interval=self.settlement_poll_interval,
            )
            record.status = "settled"
        except Exception as error:
            record.error = error
            record.status = "settlement_unknown" if isinstance(error, asyncio.TimeoutError) else "settlement_error"
        finally:
            record.completed_at = time.time()
            await self._release_active()

    async def _release_active(self):
        async with self._state_lock:
            self._active_orders = max(0, self._active_orders - 1)

    async def _wait_settlements(self):
        while self._settlement_tasks:
            tasks = tuple(self._settlement_tasks)
            done, pending = await asyncio.wait(tasks)
            self._settlement_tasks.difference_update(done)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *args):
        await self.stop()


AutoExecutor = ExecutionOrchestrator
