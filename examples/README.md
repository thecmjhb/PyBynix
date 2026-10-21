# Examples

Install the package from the checkout before running an example. Public examples need
no credentials. Authenticated examples use a session created by `pybynix login`.

| Example | Action |
| --- | --- |
| `public_data.py` | Read a public tick and a short live stream |
| `live_data.py` | Public data by default; optional environment-based authentication |
| `history_export.py` | Export one hour of server-time history to CSV/JSON |
| `session_login.py` | Read a saved session and account balances |
| `multiple_clients.py` | Independent named clients in a pool |
| `sentiment_stream.py` | Observe the explicitly derived sentiment model |
| `demo_order.py` | One explicit demo order; requires --execute-demo-order and amount/direction |
| `order_recovery.py` | Reconcile a journal reference without resubmitting |
| `orchestration.py` | Safe callback orchestration; dry-run by default |

```console
python examples/public_data.py
python examples/history_export.py --output candles.json
python examples/multiple_clients.py
pybynix login
python examples/session_login.py
```

The demo-order example uses `BYNIX_TOKEN` supplied privately. No example submits a real
or funded order automatically. Keep order journals and session files private.
