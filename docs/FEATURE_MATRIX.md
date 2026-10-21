# PyBynix feature and fallback matrix

PyBynix is the Bynix Broker integration client. It is intentionally modeled after the
useful *integration categories* of libraries such as PyQuotex, but it is not a drop-in
Quotex clone and does not invent an endpoint when Bynix behavior has not been observed.

## Capability coverage

| Domain | PyBynix capability | Alternative/fallback path | Evidence boundary |
| --- | --- | --- | --- |
| Authentication | Imported host-bound session, password login, browser-assisted login, OTP callback, `AuthChain` | Provider refresh after an eligible authenticated GET failure | Offline-tested; password/browser/session flows live-observed on the owner's demo setup |
| Host selection | Bundled trusted domains and `connect_any` | Fresh authentication per fallback host; existing tokens are never moved silently | Offline-tested; selected alternate hosts live-observed |
| Market transport | Socket.IO/WebSocket stream | REST tick polling in `auto` mode when WebSocket is unavailable, disconnected, or stale | Offline-tested and demo-soak observed |
| Market history | Recent candles, bounded paginated history, ordered bounded-concurrency candle batch, exact asset lookup, payouts | Page-boundary deduplication, exclusive time bounds, opt-in bounded cache for explicit historical boundaries | Offline-tested; catalog/history live-observed |
| Live streams | Ticks, forming/closed candles, account events, connection events | Bounded per-consumer queues; explicit overflow and reconnect-gap events | Offline-tested; live ticks and reconnect observed |
| Explicit execution | Demo-default order, real/funded guards, settlement wait, early close | One POST only; journal + pending/history reconciliation after uncertainty | Offline-tested; demo execution and early close observed |
| Callback automation | `ExecutionOrchestrator` / `AutoExecutor` for caller-owned explicit intents | Dry-run default, demo-only default, cooldown, concurrency cap, settlement tracking; never retries uncertain orders | Offline-tested only; no automatic strategy or signal is supplied |
| Account state | Profile, balances, balance breakdown, local account default, funded/tournament metadata | Profile health runs independently from market supervision | Offline-tested; profile/balance paths live-observed |
| Network routing | HTTP CONNECT, SOCKS5/SOCKS5h, static DNS, explicit DNS servers | TLS hostname verification remains enabled; proxy and custom DNS are mutually exclusive | Offline-tested; local authenticated proxy/DNS lab observed |
| Derived data | Platform-style derived sentiment, candle sentiment, export, pandas conversion, resampling | No claim that sentiment equals customer positioning | Offline-tested; sentiment is a derived display helper |
| CLI | Login, diagnostics, balances, assets, candles, stream, sentiment, domains | Public data commands work without an account | Offline-tested |
| Support surface | API docs, examples, contribution/security guidance, localized READMEs | No paid support channel or SLA is implied by the open-source package | Documentation is public; support commitments are not claimed |

## Supervision order

The active market path is deliberately ordered:

1. use Socket.IO/WebSocket when available;
2. if the stream is unavailable, disconnected, or stale, poll the public REST tick endpoint;
3. independently attempt bounded Socket.IO reconnects;
4. emit a possible-gap event so the caller can resynchronize candles from history.

Profile health refresh is a separate supervisor. A slow profile response cannot hold the
market fallback loop. Both supervision polls have a maximum five-second bound and zero read
retries. Ordinary eligible GET calls may use the configured bounded retry budget; order POST
calls never retry automatically.

## Comparison with PyQuotex-style categories

The following table distinguishes comparable categories from exact parity:

| PyQuotex-style category | PyBynix status |
| --- | --- |
| Connection and authentication | Covered with Bynix-specific providers and host binding |
| Trading operations | Covered as explicit Bynix orders, settlement, reconciliation, and early close |
| Market data and deep history | Covered with Bynix candles, ticks, history pagination, and resampling |
| Account management | Covered for observed profile, balances, account selection, funded/tournament metadata |
| WebSocket | Covered through Socket.IO with REST fallback |
| Utilities and helpers | Covered through CLI, exports, pandas conversion, journals, pooling, and network options |
| Technical indicators | **Not bundled as a strategy or signal layer**; callers may use exported candles with their own analysis tools |
| Broker-specific operations | Only implemented when the Bynix endpoint and behavior were observed and validated |
| Demo balance refill / bulk random orders / automatic strategy | Intentionally not included |

“All features” therefore means all features in the verified PyBynix scope, not an unsupported
promise of one-to-one PyQuotex API compatibility. New Bynix endpoints should be added only
with an observed request/response, typed failure behavior, tests, and a dated evidence record.

## Status vocabulary

- **Implemented** — present in the public source.
- **Offline-tested** — covered without credentials or network access.
- **Live-demo observed** — exercised on the owner's Windows demo setup on a stated date.
- **Not claimed** — not inferred from another broker's library or from a method name alone.
