# Observed Bynix protocol

Observation date: 20 October 2026. Main host: `bynixbrokers.com`.
The deployed web bundle was inspected, then requests were validated against the owner's
account in demo mode. The implementation is independent; Quotex wire compatibility is
not assumed. No credential, token, session ID, account ID or trade ID appears below.

## Authentication and account

- `POST /api/auth/login`: email/password JSON; returns access_token/token_type/user.
- `GET /api/auth/me`: bearer token; profile includes demo_balance and real_balance.
  Some responses include refreshed_token; update the session if present.
- Web app persists its token at localStorage key `token`; the optional browser provider
  reads it after ordinary login and validates it via the profile endpoint.
- 2FA is visible in the frontend flow but not triggered by the test account. Manual
  browser completion is supported; automated OTP callback handling is not verified.

## Market data

Re-observed 26 October 2026. Market endpoints are no longer open: they require a
**market session** and the body they return is **sealed**.

### Market session

```
POST /api/market/session
  headers: Authorization: Bearer <access_token>   (optional: a guest session is issued without it)
  body:    {"webdriver": false, "lang": "en-US", "touch": 0}
  200:     {"sid": "...", "key": "<b64, 32 bytes>", "ttl": 43200, "guest": false,
            "limits": {"history_1m": 150, "symbols_1h": 60, "subs_1m": 60, "candles": 1000},
            "stream": {"e": "<sealed keyring>"}}
```

The three body fields mirror what a browser reports. Without the call, market endpoints
answer `401 {"detail": "Market session required"}`; with a stale `sid` they answer
`401 {"detail": "Market session expired"}`. Guest sessions are issued without a token and
carry tighter `limits`.

### Sealed envelopes

Every market payload arrives as `{"e": "<base64>"}`, which opens to

```
base64( nonce[12] || ciphertext || tag[16] )      ChaCha20-Poly1305, key = session `key`
```

`pybynix.crypto` opens these; it uses `cryptography` when installed and a pure-Python
RFC 8439 implementation otherwise. Nothing in the SDK seals a payload.

### Endpoints

- `GET /api/assets`: still plaintext and still open. Array; each record can contain
  trade_symbol, asset_id, symbol, name, payout_percentage, market and availability flags.
- `GET /api/synthetic/history?symbol=...&interval=1m&count=...&before=...`
  with `X-Market-Session: <sid>`: sealed; opens to
  `{symbol, interval, candles, price, server_time, source}`. Candle `timestamp` is
  seconds; `server_time` is milliseconds; `before` is passed in seconds.
- `GET /api/synthetic/tick/{symbol}?interval=1m` with the same header: sealed; opens to
  the tick object below.
- `GET /api/platform/status`: plaintext, open.

The server sometimes returns count+1 candles, including the forming candle. The SDK
sorts/deduplicates, applies an exclusive `before`, and returns at most the requested
count.

### Socket.IO

Engine.IO 4, WebSocket transport, path `/api/ws/socket.io`.
The observed handshake advertised a 25-second ping interval and a 20-second ping timeout.
The upstream Socket.IO client library owns framing, heartbeats and reconnection.

| Direction | Event | Payload |
| --- | --- | --- |
| Send | subscribe_synthetic | symbol, interval, **s** (market session id) |
| Send | unsubscribe_synthetic | symbol, interval, s |
| Receive | synthetic_tick | `{"k": <key id>, "e": "<sealed tick>"}` |
| Send | subscribe_account | token |
| Send | unsubscribe_account | none; present in web app, not needed for client shutdown |
| Receive | account_event | kind and event-specific fields |

A subscription without `s` is accepted but no ticks arrive — this was the difference
between a silent stream and a working one.

The sealed `stream` field of the market session opens to a rotating keyring:

```json
{"id": 2988341, "key": "<b64>", "next_id": 2988342, "next_key": "<b64>",
 "rotate_at": 1793005200, "epoch_s": 600}
```

A tick names its key in `k`; `id`/`next_id` cover the rotation boundary, so a client
holding both keeps working through it. An unknown `k` after `rotate_at` means the keyring
must be fetched again.

The opened tick is unchanged from the plaintext era: symbol, interval, price, timestamp,
server_time, payout, candle, closed, source, market, epoch. `closed` carries the finished
candle when a minute rolls over.

Observed account kinds: subscribed, trade_opened and trade_closed. Account subscription
uses the bearer token. An emitted subscription is not treated as proof of a future
incoming tick; callers use a deadline for the first item.

## Execution

`POST /api/trades` requires asset, trade_type, direction, amount, duration, account_type,
and entry_price. The client also sends a client_ref. A missing entry_price was rejected
with HTTP 422, establishing that the field is required. The SDK gets the quote from a
recent incoming tick or the platform history response. It does not let callers invent
entry/exit prices. Acceptance returns trade_id, entry_price, expires_at and payout/balance
metadata. The server's returned entry price is authoritative.

`GET /api/trades/pending` returns trades/count/server_time. `GET /api/trades/history`
uses account_type/page/limit. Settled demo statuses observed: won and closed.
`POST /api/trades/{id}/close-early` was demo-verified and returned success/status/refund.
`GET /api/trades/stats` returned an object.

No settlement-price submission is implemented: the SDK waits for server state and
history. Settled history did not include client_ref in the observed responses. Unknown
order outcomes are not retried and may remain unresolved without a platform ID.

## Domains and trust

`domains.json` is the 26-host snapshot published by https://status.bynix.app/ on the
observation date. 25 answered platform-status checks from this PC. Membership is a
provenance record, not a security or availability guarantee. Imported sessions are
host-bound. An explicitly configured login fallback authenticates separately per host.

## Sentiment

The inspected frontend computes a weighted candle-return component for its sentiment
display, rather than exposing a verified trader-positioning event. The SDK's
get_candle_sentiment labels its output derived_candle_momentum. No claim is made that
this equals every component of the UI sentiment bar or actual customer positions.

## SDK supervision notes (21 October 2026)

Market supervision and authenticated profile health refresh run in separate tasks. A
profile check uses a bounded timeout of at most five seconds and no read-retry budget;
market REST fallback uses the same short bound and no read retries. Therefore a slow
`GET /api/auth/me` does not deliberately hold the market fallback/reconnect loop. This
is an SDK scheduling guarantee covered by offline regression tests, not a claim about
upstream server latency.
