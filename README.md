<div align="center">
  <img src="assets/logo.png" alt="PyBynix" width="380">
  <h3>Asynchronous Bynix data and trade execution for Python.</h3>
  <p>Independent, unofficial SDK by C. M. Jubayer Hossain Bappy.</p>
</div>

PyBynix gives bot authors session authentication, historical candles, live ticks/candles,
account data and explicit order execution. It contains no trading strategy or signals.
This is an early alpha built against the observed Bynix REST + Socket.IO protocol.
It is not affiliated with Bynix or the Python Software Foundation.

## Install from this checkout

Python 3.11 or later:

```console
python -m pip install .
```

No PyPI publication is claimed. Development: `python -m pip install -e ".[dev]"`.
Optional browser login: `python -m pip install ".[browser]"` then
`python -m playwright install chromium`.

## Live data with an existing session

Set `BYNIX_TOKEN` privately in your environment. Never paste a token in an issue.

```python
import asyncio
import os
from pybynix import Bynix, Session


async def main():
    async with Bynix(session=Session(os.environ["BYNIX_TOKEN"])) as client:
        print(await client.get_balance())  # demo by default
        candles = await client.get_candles("EUR/USD OTC", interval="1m", count=20)
        print(candles[-1])
        async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
            async for tick in ticks:
                print(tick.price)
                break


asyncio.run(main())
```

## Credential login and explicit browser fallback

```python
from pybynix import AuthChain, BrowserAuth, PasswordAuth

provider = AuthChain(
    PasswordAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"]),
    BrowserAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"]),
)
# async with Bynix(provider=provider) as client: ...
```

Browser fallback occurs on an interactive challenge, not incorrect credentials or a rate
limit. Supplied session, `load_session(path)`, and custom async session providers are also
supported. Only configure a provider when you want expired-session reauthentication.
`save_session` explicitly exports a plaintext secret; protect its directory. Export the
current `client.session` if `/auth/me` rotated the token.

## Explicit demo execution

```python
from decimal import Decimal

# Inside an authenticated client context. Runs a trade only when you call it.
order = await client.place_order("EUR/USD OTC", "call", Decimal("1.00"), 5)
result = await client.wait_order_result(order["trade_id"], timeout=90)
```

Account selection belongs to each order in Bynix. `change_account("demo")` changes the
client default; it does not send a fictional account-switch request. Real-money orders
require `allow_real_trading=True` and `account_type="real"`. The client supplies no
arbitrary entry/exit price: it obtains the required entry quote from the platform's
current feed. It never automatically retries an uncertain order. Catch
`OrderOutcomeUnknown` and reconcile its `client_ref` with `reconcile_order`.

## Interfaces

| Area | Methods |
| --- | --- |
| Connection | `connect`, `close`, `reconnect`, `wait_ready`, `connection_events` |
| Account | `get_profile`, `get_balance`, `change_account`, `account_events` |
| Data | `get_assets`, `get_payouts`, `get_candles`, `get_historical_candles` |
| Streams | `subscribe_ticks`, `stream_ticks`, `stream_candles` |
| Execution | `place_order`, `buy`, `get_pending_orders`, `get_trade_history`, `get_trade_stats`, `reconcile_order`, `wait_order_result`, `close_trade_early` |
| Diagnostics | `get_platform_status`, `get_server_clock`, `metrics` |
| Derived data | `get_candle_sentiment` (momentum, **not** trader positioning) |

Use exact `trade_symbol` values from `get_assets`, e.g. `EUR/USD OTC`. Candle timestamps
and history bounds are Unix **seconds**; tick and server times are Unix **milliseconds**.
Current candles can be incomplete. `stream_candles(closed_only=True)` uses the server's
closed-candle field. History bounds are `[start, end)` and requests are bounded by a
page limit; historical availability is controlled by Bynix.

Each client owns its state. Multiple clients are permitted subject to server session
limits. Streams resubscribe after reconnect; the connection event reports a possible gap.
Queues are bounded; a slow consumer receives `StreamOverflow` and must resync history.
Use context managers or `aclose()` to unsubscribe.

## Current limits and evidence

See [validation](docs/VALIDATION.md) for checks actually completed and
[protocol](docs/PROTOCOL.md) for observed wire behavior. This alpha does not claim premium
feature parity, fastest execution, strategy profitability or permanent compatibility.
Upstream sentiment is unverified; `get_realtime_sentiment` raises `CapabilityError`.
Funded/tournament execution and arbitrary expiry times are not supported in this version.
Close-early is server-dependent, and may be rejected.

Hosts originate from the platform's status page; a listed host is not a current health
guarantee. Sessions are host-bound; cross-host token forwarding is rejected.
`Bynix.connect_any(hosts, provider=...)` tries configured trusted hosts at login, with
separate authentication on each. It never silently migrates a submitted order.
`pybynix.domains.check_domains()` performs bounded, read-only reachability checks.

`proxy="http://..."` routes HTTP and WebSocket through an HTTP(S) CONNECT proxy.
`dns_overrides={"bynixbrokers.com": "<verified IP>"}` overrides DNS for both paths while
retaining TLS hostname verification. Proxy and DNS override modes are mutually exclusive.
If using credential login, configure the same network settings on `PasswordAuth` as well.
SOCKS proxies and arbitrary custom DNS servers are not implemented.

Acknowledged orders retain a bounded in-memory `client_ref` to platform-ID mapping.
Observed settled history omits `client_ref`: an unacknowledged order that has already
settled may remain unknown when only that reference is available. Persist acknowledged
platform IDs in your application. No exactly-once execution guarantee is claimed.

English / [বাংলা](README.bn.md). [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md).
