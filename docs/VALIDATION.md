# Validation record - 20 October 2026

Code version: 0.1.0a1. Local environment: Windows, Python 3.13.2. This is an early alpha,
not a claim of complete premium parity or production certification. No real funds were
used. No controlled result comparing PyBynix speed with PyQuotex has been obtained.

## Live checks completed on the owner's PC

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

## Offline checks

24 automated tests passed at this stage. They cover unsafe host/session configuration,
token-safe repr/errors, queue closure and overflow, concurrent refresh, no blind retry
of uncertain orders or rejected POSTs, amount/real-trading guards, candle normalization,
client isolation, challenge-only auth fallback, recorded tick dispatch, reconnect gap
notification and unsupported upstream sentiment.

These tests simulate fault conditions and do not establish all corresponding server
behaviors. CI is configured for Windows/Linux and Python 3.11/3.13; remote CI has not run.

## Measurements

A read-only local run issued 20 candle requests with a 0.1-second pause between calls.
Observed median end-to-end request time was 66.916 ms, with a 62.532-160.711 ms range.
This includes network and server work, and is not order execution latency.

The offline replay benchmark processed one recorded public-market tick, with JSON
decoding and Decimal model construction. After 1,000 warmups, ten batches of 10,000
iterations produced a median batch-average cost of 8.847 microseconds per event.
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
- Linux and other Python versions, verified by an actual run rather than configuration.

Release status: local alpha; not uploaded to GitHub or PyPI, and no paper deposit made.
