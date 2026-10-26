# Changelog

## 1.0.2 - 26 October 2026

Fixes order placement, which 1.0.1 broke. `place_order` needs a quote from Bynix, and
that quote was still requested from the pre-session endpoint, so every order failed with
`401 Market session required` even though candles and ticks worked.

- `place_order`, `get_server_clock` and `get_candle_sentiment` now fetch their quote
  through the market session like every other market call.
- Verified end to end on a demo account: order accepted, listed in pending trades, early
  close accepted, balance reconciled.
- Tests: a regression test asserts the order quote carries `X-Market-Session`, and the
  shared test mock (`tests/_marketmock.py`) now answers the market bootstrap so a mock
  can no longer hide this class of bug.

## 1.0.1 - 26 October 2026

Market data broke: Bynix now gates it behind a market session and seals the response
body. This release restores `get_candles`, `get_tick` and `subscribe_ticks` against the
observed protocol.

- Added the market session handshake. Market endpoints answer
  `401 {"detail": "Market session required"}` without an `X-Market-Session` header; the
  client now obtains one from `POST /api/market/session` and refreshes it on expiry.
  Guest sessions work without an account, with tighter limits.
- Added sealed-envelope support (`pybynix/crypto.py`, ChaCha20-Poly1305 per RFC 8439).
  Market responses arrive as `{"e": "<base64 nonce||ciphertext||tag>"}` and are opened
  with the session key. Verified against the RFC 8439 test vector; uses `cryptography`
  when installed and a self-contained pure-Python implementation otherwise, so there is
  no new mandatory compiled dependency.
- Socket subscriptions now carry the market session id (`s`). Without it the server
  accepts the subscription and never sends a tick.
- Tick payloads are sealed with a rotating keyring (`{"k": <id>, "e": ...}`). The keyring
  comes from the sealed `stream` field of the market session and is refreshed across
  `rotate_at`.
- New `MarketSessionManager` (`pybynix/market.py`) with single-flight creation, TTL-based
  refresh, and the platform's own limits exposed as `client.market_limits`.
- A market session can be shared: `Bynix(market_session=...)`. This is required in
  practice, because creating a second session for the same account invalidates the first,
  so a REST client and its socket shards must share one.
- A Cloudflare challenge on a market call now raises `ChallengeRequired` instead of a
  bare `APIError(403)`, so callers can tell an IP/browser block from an auth failure.
- Added `AccessDenied` for Cloudflare firewall denials (error 1020). A deny rule is not a
  challenge and cannot be solved, so it is raised without retrying, which would only
  extend the block.
- Hosts that predate the change still work: a 404 from `market/session` falls back to the
  old open endpoint.
- Every README edition gained a "What changed in this release" note, generated from
  `docs/i18n/locales/en.json`.
- Protocol documentation rewritten for the session handshake, the envelope format and the
  rotating stream keys. Tests: `test_crypto.py`, `test_market.py`, and the client mocks
  now model the session bootstrap. `cryptography` moved into the `dev` extra so CI covers
  both the native and the pure-Python crypto paths.

## Unreleased - 24 October 2026

- Documentation only; no change to the published 1.0.0 package code.
- Research preprint published on Zenodo (DOI 10.5281/zenodo.23611267, not peer reviewed);
  linked from all 20 README editions with a DOI badge and BibTeX entry, and set as the
  preferred citation in `CITATION.cff`.
- Architecture and Protocol documents are now linked from every README edition.
- Logo assets regenerated at 2x resolution with clean transparent edges (no light fringe on
  dark backgrounds).
- Fixed a CI-only test flake: the profile-supervision test assumed `time.monotonic()` was
  already larger than the refresh interval, which fails on freshly booted Linux runners.

## 1.0.0 - release-preparation snapshot, 22 October 2026

- Independent REST + Socket.IO client based on observed Bynix behavior.
- Explicit session, password, browser and custom providers; challenge-only fallback.
- Typed market ticks/candles, history, account events and bounded stream subscriptions.
- Demo-default execution, settlement lookup, early close and uncertain-order reporting.
- HTTP CONNECT proxy, static DNS overrides and trusted login-host chain.
- Twenty maintained README editions, localized quickstarts, diagnostics CLI, replay tests and
  documented measurements.
- Profile health refresh is isolated from market fallback with bounded, zero-retry
  supervision polling; regression coverage includes a deliberately slow profile response.
- All public SDK capabilities are included in the MIT package without a paid or premium
  feature tier. Added pure Decimal SMA/EMA/RSI/MACD/Bollinger/ATR/stochastic helpers,
  `buy_and_wait`, the explicit `sell` early-close alias, and caller-owned automation.
- Added fail-closed orchestration: unknown order outcomes, journal failures, missing trade IDs,
  and settlement errors pause new submissions; idle sources stop cleanly and in-flight order
  submissions are drained rather than blindly canceled.
- Added bounded-concurrency independent candle batching and opt-in historical-page caching;
  latest/forming queries are never cached.
- Completed source-fingerprinted three-stream demo soak: 600.801864 seconds, one scheduled
  reconnect, 3,597 ticks, zero malformed/duplicate ticks, on the owner's Windows demo setup.
- Selected green/gold concept 07 branding. The GitHub repository is now public; PyPI publication
  remains a separate release action. No premium parity or production certification is claimed.
