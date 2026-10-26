# Troubleshooting

| Symptom | Action |
| --- | --- |
| `Market session required` | The host issues no market session. Update the package; market data has required a session since 26 October 2026. |
| `Market session expired` | The client refreshes automatically. If it persists, the `sid` is being rejected — check the clock and retry with a fresh client. |
| Candles or ticks return empty | Market payloads are sealed; an `{"e": ...}` body that does not open means the session key and the session id are out of step. Recreate the client. |
| `ChallengeRequired` on a market call | Cloudflare is challenging this IP, not your account. `bynix.app` served market data without a challenge where `bynixbrokers.com` did not; try the other host or a different egress. |
| `AccessDenied` (error 1020) | Cloudflare's firewall is refusing this IP outright. There is no challenge to solve and no cookie to collect, so the SDK raises immediately instead of retrying. Use another network (mobile hotspot, proxy) or wait for the rule to expire. |
| `Encrypted ticks` counter stays at 0 | Subscriptions without the market session id (`s`) are accepted and then ignored. Update the package. |
| AuthenticationError with an imported token | Use `pybynix login` or configure a password/browser provider. Check that the session host matches. |
| ChallengeRequired | Supply an OTP callback or enable the explicit owner-interactive browser provider. |
| Browser executable missing | Install `.[browser]`, then run `python -m playwright install chromium`. |
| SOCKS/DNS integration missing | Install `.[network]`; install extras in the same Python environment that runs your application. |
| Unknown or ambiguous symbol | Read `get_assets()` and use the exact `trade_symbol`, including ` OTC` where present. |
| Market disallows an order | Inspect the asset's availability flags and `get_trading_rules()`. Server restrictions remain authoritative. |
| Minimum-duration error | Use a duration meeting the account/market rule; duration values are seconds. |
| Empty historical range | Check UTC seconds, interval and the platform's retained history; narrow the range. |
| History page limit reached | Narrow the time window or explicitly raise max_pages within platform limits. |
| Prices continue while WebSocket is down | Auto mode can use REST polling. Check connection events and metrics. |
| No stream item arrives | Put a deadline around `anext(subscription)` and check market status, transport and symbol. |
| StreamOverflow | Close that subscriber, resync history and process faster or enlarge its queue. |
| OrderOutcomeUnknown | Reconcile the stored client_ref/platform ID; do not blindly resend. |
| DuplicateOrderReference | Query the existing record. Use a new reference only for an intentionally new order. |
| Real/funded order opt-in error | Set allow_real_trading explicitly; funded also needs a selected challenge_id. |
| RateLimitError | Honor retry_after when available, lower concurrency and avoid rotating around the platform limit. |
| Different-owner refresh rejected | Ensure each client and provider refer to the same account; use separate named clients. |
| Journal full | Reconcile outstanding records or choose SQLiteJournal; unknown intentions are preserved. |
| Proxy works for data but login fails | Configure the route on PasswordAuth too. Python transport settings do not configure BrowserAuth. |
| Custom DNS resolves but TLS fails | Use the correct endpoint; keep TLS verification enabled. |

Start diagnostics with `pybynix doctor`, then test a short stream or candle query.
Report the package/Python version, selected hostname, method, exception class and a
minimal sanitized reproduction. Never include tokens, passwords, proxy secrets, session
files, raw account events or unredacted HTTP/browser captures in a public report.
