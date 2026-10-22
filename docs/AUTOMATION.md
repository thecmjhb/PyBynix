# Callback orchestration

`ExecutionOrchestrator` is a small safety layer around the existing
`Bynix.place_order()` and settlement APIs. It does not contain a strategy,
signal generator, Martingale behavior, sizing model, or financial claim. The
application owns the decision callback and must return an explicit
`OrderIntent` (or `None`).

Dry-run is the default. A non-dry run is demo-only by default; set
`demo_only=False` only when the application intentionally opts out of that
guard, and keep the client's existing `allow_real_trading` checks in place.

```python
from pybynix import ExecutionOrchestrator, OrderIntent


async def decide(event):
    # Application-owned decision. The SDK does not supply a strategy.
    return None
    # Or, when the application has an explicit intent:
    # return OrderIntent("EUR/USD OTC", "call", "1.00", 5, client_ref="example-1")


runner = ExecutionOrchestrator(
    client,
    "EUR/USD OTC",
    decide,
    dry_run=True,             # safe default; no POST is made
    cooldown=5.0,
    max_concurrent_orders=1,
)

await runner.run()            # consumes client.subscribe_ticks(...)
# await runner.stop()         # use with start() for background operation
```

For an application-owned event source or offline test, pass an async iterable
to `run(events)`. `await runner.submit(intent)` is also available for a clear
intent callback or other explicit execution boundary.

When `dry_run=False`, the orchestrator calls `Bynix.place_order()` exactly once
per admitted intent. The client reserves and updates its configured journal,
and `ExecutionOrchestrator` tracks accepted trade IDs through
`wait_order_result()`. `OrderOutcomeUnknown` and `OrderJournalError` are
recorded without retrying or submitting a replacement. A missing
reconciliation match is never treated as permission to resend.

An unknown submission outcome, accepted-order journal failure, missing trade ID,
or settlement timeout/error puts the orchestrator into a fail-closed safety
pause. `runner.safety_paused`, `runner.pause_reason`, and `runner.state` expose
this state; later intents receive `skipped_safety_pause` and no order call is
made. The unresolved execution slot remains reserved, so a safety failure
cannot silently free concurrency for another exposure. A journal error still
preserves its accepted receipt in the execution record. The pause is not
automatically cleared after settlement; reconcile the situation explicitly
before constructing a new runner.

`stop()` closes sources that support `aclose()` and cancels only the intake task
when an external source is idle. In-flight order submissions are shielded and
drained rather than canceled blindly, and no new submission is admitted once
stop begins. A runner cannot be restarted after `stop()`.

Records are available through `runner.records`; statuses include `dry_run`,
`accepted`, `settled`, `unknown`, `accepted_journal_error`,
`settlement_unknown`, `settlement_error`, `skipped_cooldown`,
`skipped_concurrency`, and `skipped_safety_pause`. Callback and settlement
errors are collected in `runner.errors` for application handling. Records and
errors are bounded to 1,000 entries by default; applications with long-lived
consumers can set `max_records` and `max_errors` in the constructor.
