import httpx
import pytest

from pybynix import PasswordAuth
from pybynix.errors import AuthenticationError, ChallengeRequired, RateLimitError


@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (403, {"code": "account_disabled"}, AuthenticationError),
        (403, {"code": "challenge_required"}, ChallengeRequired),
        (429, {}, RateLimitError),
    ],
)
async def test_password_provider_distinguishes_denial_challenge_and_rate_limit(
    monkeypatch, status, body, error_type
):
    original = httpx.AsyncClient

    def factory(**kwargs):
        return original(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
        )

    monkeypatch.setattr("pybynix.auth.httpx.AsyncClient", factory)
    with pytest.raises(error_type) as error:
        await PasswordAuth("example@example.com", "not-a-real-password")("bynixbrokers.com")
    assert type(error.value) is error_type
