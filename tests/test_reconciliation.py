import httpx
import pytest

from pybynix import Bynix, Session
from pybynix.errors import AuthenticationError, ProtocolError


async def test_settled_reference_uses_acknowledged_id_and_does_not_invent_missing_match():
    def handler(request):
        if request.url.path.endswith("pending"):
            return httpx.Response(200, json={"trades": []})
        return httpx.Response(200, json={"trades": [{"trade_id": "server-order", "status": "won"}]})

    client = Bynix(session=Session("test-token"))
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://bynixbrokers.com/api/", transport=httpx.MockTransport(handler)
    )
    client._known_orders["known-ref"] = "server-order"
    try:
        known = await client.reconcile_order(client_ref="known-ref")
        unknown = await client.reconcile_order(client_ref="unacknowledged-ref")
        assert known["trade_id"] == "server-order"
        assert unknown is None
    finally:
        await client.close()


async def test_login_host_fallback_closes_failed_client_and_stops_on_denial():
    attempted, closed = [], []

    class FakeClient(Bynix):
        async def connect(self):
            attempted.append(self.host)
            if self.host == "bynixbrokers.com":
                raise ProtocolError("network failed")
            return self

        async def close(self):
            closed.append(self.host)
            await super().close()

    async def provider(host):
        return Session("token", host)

    client = await FakeClient.connect_any(["bynixbrokers.com", "bynix.app"], provider=provider)
    try:
        assert attempted == ["bynixbrokers.com", "bynix.app"]
        assert closed == ["bynixbrokers.com"]
    finally:
        await client.close()

    class DeniedClient(FakeClient):
        async def connect(self):
            raise AuthenticationError(401)

    with pytest.raises(AuthenticationError):
        await DeniedClient.connect_any(["bynixbrokers.com", "bynix.app"], provider=provider)
