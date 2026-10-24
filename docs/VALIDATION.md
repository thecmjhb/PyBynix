# Validation record - 22 October 2026

Code version: 1.0.0. Local environment: Windows, Python 3.13.2. This is a
release-preparation validation record, not a claim of complete premium parity or production
certification. No real funds were used. No controlled result comparing PyBynix speed with
PyQuotex has been obtained.

## Live checks completed on the owner's PC (20 October 2026)

| Capability | Observed result |
| --- | --- |
| Email/password HTTPS login | Authenticated successfully |
| Imported bearer session | Profile, data and WebSocket connection succeeded |
| Invalid imported session + configured password provider | Reauthenticated and connected |
| Browser login | Headless Chromium form login, token extraction and authenticated profile succeeded |
| Assets | 148 catalog records returned; not every individual market stream tested |
| Current/historical candles | Parsed, bounded and sorted; range query tested |
| Live ticks | EUR/USD OTC and GBP/USD OTC received |
| Explicit reconnect | Existing tick subscription resumed after a client reconnect |
| Independent clients | Different symbol subscriptions; closing one preserved the other's stream |
| Account event stream | subscribed, trade_opened and trade_closed received |
| Demo execution | Two separate $1, five-second orders accepted and settled in history |
| Demo early close | One $1, sixty-second order accepted, closed early and reconciled as closed |
| Acknowledged client reference lookup | In-memory platform-ID mapping reconciled a settled order |
| Unacknowledged client reference after settlement | Not guaranteed: observed settled history lacks client_ref |
| HTTP CONNECT proxy | REST and WebSocket passed through two observed tunnels on a local test proxy |
| Static DNS override | REST and incoming tick succeeded while TLS hostname checking remained enabled |
| Platform domain reachability | 25 of 26 published candidates answered a read-only status probe |
| Alternate host | Separate password login, authenticated profile and live tick succeeded on bynix.app |
| Short continuous observation | 181.664 seconds, 356 ticks, zero malformed ticks, one connection; not a long stability study |
| Distribution | Wheel/source archive built; wheel installed in a clean environment and fetched three live candles |
| Stats/status/clock | Returned expected object shapes; clock offset remains an estimate |

Three virtual-money demo orders were used in total. Their outcomes are functional test
observations, not a sample supporting any profitability conclusion. The early-close
refund was server-determined. No deposits, withdrawals or account-profile changes were
performed. Private identifiers and session values are excluded from the public record.

## Final source-fingerprinted demo soak (21 October 2026)

The frozen Windows checkout ran the read-only multi-stream soak to completion for
**600.801864 seconds** (10 minutes). It subscribed to three demo-account market streams:
`EUR/USD OTC` at 15 seconds, `GBP/USD OTC` at 1 minute, and `BTC/USD OTC` at 5 seconds.
The run received 3,597 ticks (1,199 per stream), observed 170 closed candles in total,
and crossed one scheduled five-minute reconnect boundary. The source SHA-256 map captured
at start and end was identical (`source_unchanged: true`).

Observed soak metrics were: two total Socket.IO connections, zero malformed ticks, zero
duplicate ticks, zero read retries, zero REST fallback polls, and a maximum measured tick
arrival gap of 1.388343 seconds. Tracemalloc reported a peak Python heap of 3,549,450
bytes. This is one completed Windows run against one demo account and three symbols; it
does not establish multi-hour stability, all-market coverage, Linux support, or guaranteed
recovery. The raw report remains private and is not a release input.

## Offline checks

100 automated tests passed on Python 3.13.2 in the dedicated project environment and 100
passed on Python 3.11.17 in the local private/env311 environment. They cover unsafe
host/session configuration, token-safe repr/errors, queue closure and overflow, concurrent
refresh, no blind retry of uncertain orders or rejected POSTs, amount/real-trading guards,
candle normalization, client isolation, challenge-only auth fallback, recorded tick dispatch,
reconnect gap notification, documentation completeness, release exclusions, and the
regression where a slow profile health refresh must not stall market fallback. The additive
indicator suite covers Decimal SMA/EMA/RSI/MACD/Bollinger/ATR/stochastic semantics, and
client convenience tests cover one-place/one-wait composition plus `sell` delegation. The
automation suite covers dry-run/demo guards, cooldown/concurrency limits, fail-closed pause
states, idle-source shutdown, in-flight submission draining, and bounded record retention.
Ruff passed with `ruff check .`.

The focused indicator/client run passed 29 tests on the same Python 3.11.17 environment.
These are offline tests only: they use no credentials, saved sessions or real-money orders,
and do not establish live endpoint behavior for the new indicators, convenience wrappers, or
automation layer.

These tests simulate fault conditions and do not establish all corresponding server
behaviors. CI is configured for Windows/Linux and Python 3.11/3.13; remote CI has not run.

## Measurements

A read-only local run issued 20 candle requests with a 0.1-second pause between calls.
Observed median end-to-end request time was 66.916 ms, with a 62.532-160.711 ms range.
This includes network and server work, and is not order execution latency.

The offline replay benchmark processed one recorded public-market tick, with JSON
decoding and Decimal model construction. After 1,000 warmups, ten batches of 10,000
iterations produced a median batch-average cost of 9.714 microseconds per event.
The metric is not a per-event tail-latency percentile and excludes socket delivery,
dispatch queues and trading. Raw results are in benchmarks/results/.

## Outstanding validation

- Extended two/eight-hour stability studies, real network outage recovery and exhausted
  reconnect attempts under controlled conditions.
- Multi-owner account isolation (only one owner account was supplied).
- Interactive CAPTCHA/2FA completion, revoked-session behavior and server rate limits.
- Authenticated production proxy services, SOCKS, arbitrary DNS servers and authenticated
  sessions across all published alternate domains (two hosts tested).
- Every asset/timeframe, live-market-session boundaries, funded/tournament accounts and
  platform updates after this observation date.
- Live behavior on Linux and on Python versions other than 3.11/3.13. The offline suite
  (100 tests) passed in GitHub Actions on `ubuntu-latest` and `windows-latest` with Python
  3.11 and 3.13 (commit `0d0eff8`, 24 October 2026); no live demo run was made on Linux.

Release status: version 1.0.0. Source on GitHub (`https://github.com/thecmjhb/PyBynix`),
package on PyPI (`https://pypi.org/project/pybynix/`), and the research paper deposited as a
preprint on Zenodo (`https://doi.org/10.5281/zenodo.23611267`; not peer reviewed).
