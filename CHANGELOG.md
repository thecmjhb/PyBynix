# Changelog

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
- Selected green/gold concept 07 branding. The public checkout remains unpublished; no premium
  parity or production certification is claimed.
