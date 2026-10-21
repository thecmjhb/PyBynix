# Changelog

## 1.0.0 - release-preparation snapshot, 21 October 2026

- Independent REST + Socket.IO client based on observed Bynix behavior.
- Explicit session, password, browser and custom providers; challenge-only fallback.
- Typed market ticks/candles, history, account events and bounded stream subscriptions.
- Demo-default execution, settlement lookup, early close and uncertain-order reporting.
- HTTP CONNECT proxy, static DNS overrides and trusted login-host chain.
- Twenty maintained README editions, localized quickstarts, diagnostics CLI, replay tests and
  documented measurements.
- Profile health refresh is isolated from market fallback with bounded, zero-retry
  supervision polling; regression coverage includes a deliberately slow profile response.
- Completed source-fingerprinted three-stream demo soak: 600.801864 seconds, one scheduled
  reconnect, 3,597 ticks, zero malformed/duplicate ticks, on the owner's Windows demo setup.
- Selected green/gold concept 07 branding. The public checkout remains unpublished; no premium
  parity or production certification is claimed.
