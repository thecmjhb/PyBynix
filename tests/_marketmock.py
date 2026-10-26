"""Shared mock helper: a mocked Bynix transport must answer the market bootstrap.

Market calls carry `X-Market-Session`, so a handler that only knows the endpoint under
test would otherwise answer the session request with its catch-all response.
"""

import base64
import inspect

import httpx

SESSION = {
    "sid": "test-market-sid",
    "key": base64.b64encode(b"k" * 32).decode(),
    "ttl": 3600,
    "guest": True,
    "limits": {"history_1m": 150, "candles": 1000},
}


def market_aware(handler):
    async def wrapper(request):
        if request.url.path.rstrip("/").endswith("/market/session"):
            return httpx.Response(200, json=SESSION)
        result = handler(request)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapper
