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

- `GET /api/assets`: array; observed 148 records. Each can contain trade_symbol,
  asset_id, symbol, name, payout_percentage, market and availability flags.
- `GET /api/synthetic/history?symbol=...&interval=1m&count=...&before=...`:
  symbol, interval, candles, price, server_time and source. Candle timestamp is seconds;
  server_time is milliseconds. `before` is passed in seconds.
- The server sometimes returns count+1 candles, including the forming candle. The SDK
  sorts/deduplicates, applies exclusive before and returns at most the requested count.
- `GET /api/platform/status`: trading_enabled and maintenance_mode.

## Socket.IO

Engine.IO 4, WebSocket transport, path `/api/ws/socket.io`.
The observed handshake advertised a 25-second ping interval and 20-second ping timeout.
The upstream Socket.IO client library owns framing, heartbeats and reconnection.

| Direction | Event | Payload |
| --- | --- | --- |
| Send | subscribe_synthetic | symbol, interval |
| Send | unsubscribe_synthetic | symbol, interval |
| Receive | synthetic_tick | symbol, interval, price, timestamp, server_time, payout, candle, closed, source, market, epoch |
| Send | subscribe_account | token |
| Send | unsubscribe_account | none; present in web app, not needed for client shutdown |
| Receive | account_event | kind and event-specific fields |

Observed account kinds: subscribed, trade_opened and trade_closed. Market subscriptions
are public; account subscription uses the bearer token. An emitted subscription is not
treated as proof of a future incoming tick; callers use a deadline for the first item.

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
