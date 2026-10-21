# Recovery and transport behavior

## Market data recovery

`stream_transport="auto"` opens a persistent Socket.IO WebSocket connection. A supervisor
checks authenticated profile health and watches symbol freshness. If WebSocket is
unavailable, disconnected or stale, public REST tick requests continue the data path.
Eligible GET transport/502/503/504 errors receive bounded backoff; HTTP 429 retries only
when a bounded numeric Retry-After is available. POST orders are not retried.

The maintained Socket.IO library owns heartbeat and normal accidental reconnects. The
watchdog also revisits initial failures or server disconnects. Successful connection
re-emits current symbol/interval subscriptions and the current account token.

Choose `websocket` mode if REST fallback is unwanted, or `polling` mode if a deployment
deliberately uses REST data. A single client can have many consumers of the same upstream
symbol/interval with separate bounded queues. Limits and request spacing are configurable.

REST polling is a continuity aid, not a lossless replay of every tick. Closed-candle
transitions can occur between polls. Observe `connection_events()` and retrieve history
after a gap when a complete candle series is required.

## Backpressure

Consumers must process at the incoming rate or provision an appropriate queue size.
A queue overflow raises `StreamOverflow` and ends that consumer. Other consumers keep
their own queues. Close the failed subscription, resynchronize history, then subscribe
again. Close unused subscriptions explicitly; stopping consumption is not an unsubscribe.

## Authentication recovery

The configured provider handles a rejected authenticated GET session once. Refresh is
single-flight within a client. The account fingerprint remains bound: a provider that
returns a different owner's identity is rejected instead of silently changing accounts.
Profile health checks also propagate legitimate token rotations to the account channel.

Public market data does not require an authenticated account. Authentication errors
therefore do not turn derived market data into account-authorized trade access.

## Order recovery

Every order receives a caller reference, generated when omitted. The journal reserves
the reference before the POST and records the accepted platform ID, explicit rejection,
or unknown outcome. Reusing a reference is rejected locally with DuplicateOrderReference.
This is local duplicate protection, not an invented server-side idempotency contract.

```python
from pybynix.errors import OrderOutcomeUnknown

async def submit(client):
    try:
        return await client.place_order("EUR/USD OTC", "call", "1.00", 5)
    except OrderOutcomeUnknown as error:
        return await client.reconcile_order(client_ref=error.client_ref)
```

If reconciliation returns None, preserve the unknown record. Do not automatically resend.
The observed settled history omits client_ref; without an acknowledged or independently
correlated platform ID, a settled uncertain order may remain unresolved. Similar time,
asset and amount are not conclusive identification.

Use `SQLiteJournal` for process restarts. It stores order intent and IDs under a scope
derived from the verified host and account fingerprint, keeping separate owners apart.
It stores trade metadata, not tokens/passwords. Keep its directory private. Records
marked submitting after a process crash still require reconciliation.

```python
from pybynix import SQLiteJournal

journal = SQLiteJournal("orders.sqlite")
# Supply this journal to each replacement client for the same account and host.
```

The default MemoryJournal is bounded. It evicts terminal records first and never silently
evicts unknown submissions merely to make room. Terminal server account events and result
waiting update journal state. Persist acknowledged IDs in your application as appropriate.

## Proxies and DNS

HTTP CONNECT and SOCKS5 proxy URLs may include proxy authentication. The network extra
supplies SOCKS and custom-resolver dependencies. A configured proxy carries both HTTP
and WebSocket traffic; the live transport lab verifies this through separate tunnels.

Static `dns_overrides` and explicit `dns_servers` apply to both transport paths. Hostname
verification remains enabled; changing a TCP destination does not change the TLS name
or HTTP Host. Proxy mode and custom-DNS mode cannot be combined because a proxy may own
destination resolution. Browser configuration remains separate.

## Trusted-domain fallback

The package contains a dated snapshot from the platform status page. `check_domains()`
performs bounded read-only reachability checks; no credentials are sent. A reachable
domain is not proof that an imported session belongs to that host.

`connect_any` tries explicitly configured trusted hosts at login, authenticating afresh
through the supplied provider. It stops on authentication denial/rate limiting and never
migrates an in-flight order. Keep the host list controlled by the application owner.

## Reproducing transport faults

`tools/live_lab.py` is an opt-in read-only lab. It creates restricted local relays, cuts
only its own tunnels, observes REST fallback and reconnect, and tests authenticated
HTTP/SOCKS proxies plus a local DNS answer. It does not change the PC's network settings,
trade, buy challenges, join tournaments or modify account details.
