import asyncio

import httpx
import pytest

from pybynix import Bynix, MemoryJournal, Session
from pybynix.errors import OrderJournalError


@pytest.mark.parametrize("failure", [OSError("disk full"), asyncio.CancelledError()])
async def test_accepted_order_receipt_survives_journal_failure(failure):
    class FailingJournal(MemoryJournal):
        async def update(self, scope, client_ref, **fields):
            if fields.get("state") == "accepted":
                raise failure
            await super().update(scope, client_ref, **fields)

    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(200, json={"trade_id": "accepted-server-id"})
        return httpx.Response(200, json={"price": 1.2})

    client = Bynix(
        session=Session("test"),
        journal=FailingJournal(),
        validate_orders=False,
        max_requests_per_second=None,
    )
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(OrderJournalError) as error:
            await client.place_order("EUR/USD OTC", "call", 1, 5, client_ref="one")
        assert error.value.order["trade_id"] == "accepted-server-id"
        assert client._known_orders["one"] == "accepted-server-id"
        assert len(posts) == 1
    finally:
        await client.close()
