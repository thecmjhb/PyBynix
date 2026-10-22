# Python API reference

PyBynix 1.0 uses asyncio. Public entry points are exported from `pybynix`; the main
facade is also available as `pybynix.stable_api.Bynix`. The package implements Bynix's
observed REST and Socket.IO protocol independently.

## Client configuration

```python
from pybynix import Bynix, Session, PasswordAuth, SQLiteJournal

client = Bynix(
    session=Session(access_token, "bynixbrokers.com"),  # optional imported token
    provider=PasswordAuth(email, password),            # optional login / refresh
    host="bynixbrokers.com",
    account_type="demo",
    journal=SQLiteJournal("orders.sqlite"),
    stream_transport="auto",
    timeout=20,
    queue_size=256,
    poll_interval=1.0,
    profile_refresh_interval=60,
    read_retries=2,
    max_requests_per_second=10,
    max_subscriptions=256,
)
```

Use `async with client:` or `await connect()` with `close()` in a `finally` block.
The context manager closes HTTP, WebSocket, supervisor tasks and subscriptions.
A closed client is not reusable; create another instance.

| Configuration | Meaning |
| --- | --- |
| `session` | Host-bound `Session`; token is hidden from its representation |
| `provider` | Async callable receiving a hostname and returning a `Session` |
| `host` | Preferred hostname; published domains are bundled; `trusted_hosts` explicitly extends the list |
| `anonymous` | Public data only; use the clearer `Bynix.public()` factory |
| `account_type` | Default `demo`, `real` or `funded` for account-aware methods |
| `allow_real_trading` | Defaults to false; real and funded orders require true |
| `challenge_id` | Explicit funded challenge selection; no challenge is purchased automatically |
| `journal` | `MemoryJournal` by default, or opt-in `SQLiteJournal` for process restart recovery |
| `stream_transport` | `auto`, `websocket`, or `polling` |
| `timeout` | Positive finite HTTP / connect timeout in seconds |
| `poll_interval` | Polling cadence, at least 0.5 seconds; also drives supervision |
| `profile_refresh_interval` | Authenticated profile check cadence, at least 10 seconds |
| `read_retries` | 0..5 retries for eligible read transport/502/503/504 failures; default 2 |
| `max_requests_per_second` | Per-client request spacing; default 10; `None` disables spacing |
| `queue_size` | Positive per-consumer queue bound; overflow is an explicit error |
| `max_subscriptions` | Positive maximum distinct symbol/interval pairs; default 256 |
| `proxy` | HTTP(S) CONNECT, SOCKS5 or SOCKS5h proxy URL |
| `dns_overrides` | Mapping of original hostnames to explicit IP addresses |
| `dns_servers` | Explicit resolver IPs; requires the network extra |
| `dns_port` | Resolver port, default 53 |

Proxy mode and custom DNS mode are mutually exclusive. Route authentication through the
same settings on `PasswordAuth`. Proxy/DNS options do not implicitly configure a browser.

### Factories and lifecycle

```python
Bynix.public(stream_transport="auto")
Bynix.from_token(access_token, host="bynixbrokers.com")
Bynix.from_session_file("session.json", provider=provider)
await Bynix.connect_any(["bynixbrokers.com", "bynix.app"], provider=provider)
```

`connect_any` returns an already connected client. It authenticates separately per trusted
host and falls back on transport errors. Authentication denial and rate limits stop the
chain. A running order never migrates silently. `set_session(token=...)` or the alias
`set_session(ssid=...)` is available before connecting or binding an account. Here SSID
means the Bynix access token, not the Engine.IO or Socket.IO connection identifier.

`connected` reports readiness. `wait_ready(timeout=...)` waits for readiness.
`reconnect()` explicitly rebuilds the connection and restores existing subscriptions.
`metrics` contains request/tick/reconnect/fallback counters without credentials.

## Authentication

- `PasswordAuth(email, password, proxy=None, dns_overrides=None, dns_servers=(), dns_port=53, otp_callback=None)`.
- `BrowserAuth(email=None, password=None, headless=False, timeout=180, profile_dir=None)`.
- `AuthChain(provider1, provider2, ...)`: falls back only on an interactive challenge.
- `load_session(path)` and `save_session(session, path)`: explicit JSON import/export.
- `Session(token, host="bynixbrokers.com")`: immutable token container.

See [authentication](AUTHENTICATION.md) for complete examples, challenge behavior and
browser setup. Session export is an opt-in plaintext secret file written atomically.

## Accounts and platform information

| Method | Result |
| --- | --- |
| `await get_profile()` | Platform profile dict, excluding `refreshed_token`; legitimate rotation updates `client.session` |
| `await get_balance(account_type=None)` | Decimal; default account; real uses `total_balance` when available |
| `await get_balance_breakdown()` | Available demo/real/bonus/deposit/profit/total/withdrawable fields as Decimal |
| `await change_account(account_type)` | Changes this client's local default; Bynix selects account per order |
| `await get_platform_status()` | Public `trading_enabled` and `maintenance_mode` object |
| `await get_trading_rules()` | Current authenticated platform restrictions and duration/stake limits |
| `await get_server_clock(symbol="EUR/USD OTC")` | Server milliseconds, measured RTT and estimated offset/uncertainty |
| `await get_funded_plans()` | Public plans/models/rules catalog |
| `await get_funded_challenges()` | Owner challenge list |
| `await get_active_funded()` | Active challenge or null |
| `await get_funded_challenge(challenge_id)` | Selected challenge response |
| `await get_tournaments()` | Public active/upcoming tournaments |
| `await get_my_tournaments()` | Owner tournament memberships |
| `await get_tournament(tournament_id)` | Public tournament detail |

A funded balance requires an explicit client `challenge_id`. Reading funded metadata
does not buy a challenge or enroll the owner. Monetary values use the platform's base
currency; display currency metadata is available in the profile and is not an implicit
conversion of order amounts.

## Assets and payouts

`await get_assets(include_inactive=False)` returns platform asset dictionaries including
`trade_symbol`, `symbol`, `name`, `asset_id`, `market`, `is_otc`, `is_active`, availability
flags and `payout_percentage` when exposed. `get_asset(symbol)` searches exact trade
symbol/name/ID; a plain symbol is accepted only if unambiguous. It caches the catalog for
up to 60 seconds.

`get_payouts()` returns catalog payout percentages as Decimal. These are catalog values;
live `Tick.payout` and the accepted order's payout may differ due to current platform
conditions or account benefits. The server's final order response is authoritative.

## Candles and prices

```python
await client.get_tick("EUR/USD OTC", interval="1m")
await client.get_candles("EUR/USD OTC", interval="1m", count=100, before=None)
await client.get_candles_batch(
    [
        {"symbol": "EUR/USD OTC", "interval": "1m", "count": 200},
        {"symbol": "GBP/USD OTC", "interval": "5m", "count": 100, "before": unix_end},
    ],
    max_concurrency=4,
)
await client.get_historical_candles(
    "EUR/USD OTC", start=unix_start_seconds, end=unix_end_seconds,
    interval="1m", max_pages=100,
)
```

- `get_tick` uses the public REST tick endpoint and returns `Tick`.
- Candle `count` is an integer 1..500. The SDK sorts and deduplicates platform output,
  filters exclusive `before`, and returns at most `count` records.
- `get_candles_batch(requests, max_concurrency=4, cache_ttl=0)` fetches independent
  candle queries concurrently, returns lists in request order, and limits in-flight
  history calls to 1..32. Each request is a mapping with only `symbol`, `interval`,
  `count` and `before`; the helper reuses the observed single-history endpoint rather
  than assuming an unsupported bulk endpoint or page layout.
- `cache_ttl` is opt-in and capped at 3,600 seconds. It caches only calls with an
  explicit `before` boundary (historical pages); latest/forming-candle queries are
  never cached. The in-memory cache is bounded to 256 query keys and is per client.
- `start`, `end`, `before`, and `Candle.timestamp` are Unix **seconds** in UTC. History
  ranges are `[start, end)`. Tick and server timestamps are **milliseconds**.
- `Candle` fields are timestamp, open, high, low, close and volume. Prices and volume
  are Decimal. A most-recent candle may still be forming.
- `get_historical_candles` walks backwards, removes page-boundary duplicates and enforces
  a page bound. A reached bound raises `ProtocolError`; narrow the range or raise the bound.
- Retention and market availability remain properties of the platform. Empty history is
  returned as an empty list, not synthetic data.

The observed chart intervals are `5s`, `10s`, `15s`, `30s`, `1m`, `5m`, `15m`, `30m`, `1h`.
Resampling to larger integer multiples is available explicitly through `resample_candles`.

### Pure technical indicators

The package exports `sma`, `ema`, `rsi`, `macd`, `bollinger_bands`, `atr` and
`stochastic`, plus descriptive aliases such as `simple_moving_average` and
`average_true_range`. These are synchronous, pure helpers over caller-owned
`Candle` or Decimal-like data. They preserve input order, return lists aligned to
the input length, and use `None` for warm-up positions without enough observations.
They never fetch data, place orders, emit signals, choose a direction or implement
a strategy/Martingale policy.

| Helper | Exact semantics |
| --- | --- |
| `sma(values, period=14, field="close")` | Arithmetic mean over each trailing window; Candle inputs use the selected OHLCV field. |
| `ema(values, period=14, field="close")` | SMA-seeded EMA with smoothing factor `2 / (period + 1)`. |
| `rsi(values, period=14, field="close")` | Wilder-smoothed RSI from close changes; flat/lossless/gainless windows are 50/100/0. |
| `macd(values, fast_period=12, slow_period=26, signal_period=9, field="close")` | `MACDResult(macd, signal, histogram)` using SMA-seeded EMAs; fast period must be less than slow period. |
| `bollinger_bands(values, period=20, multiplier=2, field="close")` | `BollingerBandsResult` using population standard deviation (`divide by period`) around the SMA. |
| `atr(candles, period=14)` | Wilder ATR from Candle true ranges; the first range is high-low, then previous-close gaps are considered. |
| `stochastic(candles, period=14, smooth_period=3)` | `StochasticResult` with 0..100 %K and an SMA-smoothed %D; flat ranges are 50. |

All calculations retain `Decimal` arithmetic. Inputs must be finite; periods must
be positive integers. These helpers describe measurements only: any interpretation,
signal, risk control or execution remains the caller's responsibility.

## Streaming

```python
async with await client.subscribe_ticks("EUR/USD OTC", interval="15s") as ticks:
    async for tick in ticks:
        print(tick.price, tick.candle, tick.closed)
```

`Tick` fields: symbol, interval, price, timestamp, server_time, candle, closed and payout.
The candle fields are `Candle` or None. `closed` represents a server-provided completed
candle, not a guarantee inferred from the local wall clock.

`stream_ticks(symbol, interval="1m")` and `stream_candles(symbol, interval="1m",
closed_only=False)` are async generators. Close generators/subscriptions when stopping
early. `closed_only=True` emits only the explicit server closed-candle field.

`account_events()` and `connection_events()` return local async subscriptions. Use them
as context managers. Account events retain platform dictionaries; connection events are
`ConnectionEvent(kind, generation, detail)`. Reconnect events disclose possible gaps.
Auto mode polls REST when WebSocket is unavailable or a symbol is stale. Polling can
miss intermediate ticks or closed-candle transitions; resync candles when continuity matters.

Queues are bounded. `StreamOverflow` terminates a slow consumer instead of silently
claiming a complete stream. New subscribers share the upstream symbol/interval subscription
and retain independent queues. Duplicate/out-of-order tick suppression is per stream.

## Sentiment

`get_realtime_sentiment(symbol)` returns integer `up`/`down` percentages summing to 100,
plus candle_pressure, tick_ema, samples, source and is_trader_positioning. The method
implements the observed display model: 65% weighted candle pressure and 35% tick-direction
EMA (alpha 0.06), rounded and clamped to 5..95. Candle pressure refreshes every 60 seconds.
The stream's starting state depends on ticks observed by this client.

`stream_sentiment(symbol)` yields this model as ticks arrive. `get_candle_sentiment(symbol,
count=30)` returns a separate candle-only up/down fraction in 0..1. Both are explicitly
derived market measures, not counts of actual customer positions.

## Trade execution

```python
order = await client.place_order(
    "EUR/USD OTC", "call", "1.00", 5,
    account_type="demo", client_ref="my-unique-reference",
)
result = await client.wait_order_result(order["trade_id"], timeout=90)
```

`place_order(symbol, direction, amount, duration, account_type=None, client_ref=None,
challenge_id=None)` takes call/put, a positive amount with at most two decimal places,
and integer duration seconds (at least 5). `buy(amount, asset, direction, duration,
**kwargs)` is a convenience wrapper with familiar argument ordering.

`buy_and_wait(amount, asset, direction, duration, account_type=None, client_ref=None,
challenge_id=None, timeout=120, poll_interval=2)` composes exactly one `place_order`
call with `wait_order_result`. It returns `{"order": accepted_receipt, "result":
terminal_history_row}` and never retries the order POST. `sell(trade_id)` is an explicit
early-close alias that delegates only to `close_trade_early`; it is not a separate
sell-direction endpoint and does not promise a refund. If settlement times out after
acceptance, no second POST is attempted; use `reconcile_order` or `unresolved_orders`
for caller-owned follow-up.

The SDK validates known availability flags and live trading rules, obtains an entry
quote from Bynix, reserves the journal reference and sends exactly one POST. An accepted
response includes a platform `trade_id` and caller `client_ref`. It is not a settled result.
Non-demo execution requires explicit `allow_real_trading=True`; funded requires a selected
challenge. No real-money order is placed by installation, login, diagnostics or data examples.

| Method | Behavior |
| --- | --- |
| `get_pending_orders()` | Current platform pending-order list |
| `get_trade_history(account_type=None, page=1, limit=50)` | Page of account history; page positive, limit 1..100 |
| `get_trade_stats()` | Platform statistics response |
| `reconcile_order(trade_id=None, client_ref=None, account_type=None, max_pages=5)` | Matching pending/history row or None |
| `wait_order_result(trade_id, account_type=None, timeout=120, poll_interval=2)` | Terminal history row; bounded by timeout |
| `close_trade_early(trade_id)` | Verified owned order; sends one early-close request; server decides eligibility/refund |
| `buy_and_wait(...)` | One order placement followed by bounded settlement lookup; returns receipt and terminal row |
| `sell(trade_id)` | Alias to `close_trade_early`; verifies and early-closes an existing trade |
| `unresolved_orders()` | Local journal records needing follow-up |

Terminal statuses handled are won, lost, draw, refunded and closed. Early close does not
promise a full refund. Read [recovery](RECOVERY.md) before automating order recovery.

## Callback orchestration

`ExecutionOrchestrator(client, symbol, decision_callback, interval="1m",`
`dry_run=True, demo_only=True, cooldown=0.0, max_concurrent_orders=1,`
`settle=True, settlement_timeout=120.0, settlement_poll_interval=2.0)`
consumes client ticks and passes each event to the caller's sync or async
callback. The callback returns `OrderIntent` (or `None`); it supplies no
strategy or signal model. `await runner.submit(intent)` is the explicit intent
boundary for applications with their own event source.

Use `await runner.run()` for foreground operation, or `await runner.start()`
and `await runner.stop()` for graceful background operation. Dry-run is the
default and makes no order request. With `dry_run=False`, demo-only is still
the default; cooldown and `max_concurrent_orders` are enforced before the
single `Bynix.place_order()` call. Accepted orders are tracked with
`wait_order_result()`. Unknown outcomes and journal persistence failures are
recorded and never retried. See [callback orchestration](AUTOMATION.md).

## Multi-client pool

`ClientPool({name: client, ...})` owns distinct client objects, connects them concurrently,
closes all on partial startup failure, supports `pool[name]`, iteration and `balances()`.
Route an order through one explicitly selected client; the pool never broadcasts it.
Account concurrency and session validity are also subject to platform rules.

## Export and derived datasets

- `export_candles(candles, path, format=None)`: CSV/JSON, inferred from extension by default.
- `candles_to_records(candles)`: string-valued Decimal fields for exact serialization.
- `candles_to_dataframe(candles)`: optional pandas with UTC DatetimeIndex and Decimal columns.
- `resample_candles(candles, source_seconds=..., target_seconds=..., complete_only=True)`:
  UTC-aligned integer multiples; incomplete/gapped buckets are omitted by default.

## Errors

Import errors from `pybynix.errors`. API errors omit raw response bodies and secrets.
Important types are APIError (HTTP status), AuthenticationError, ChallengeRequired,
RateLimitError (retry_after), ProtocolError, StreamOverflow, DuplicateOrderReference
and OrderOutcomeUnknown (client_ref). A missing reconciliation match is not permission
to resend an uncertain trade. GET retries are bounded; order POSTs are never retried.
