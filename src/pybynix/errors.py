"""Public errors deliberately exclude raw response bodies and credentials."""


class BynixError(Exception):
    pass


class APIError(BynixError):
    def __init__(self, status: int, code: str | None = None):
        self.status = status
        self.code = code
        super().__init__(f"Bynix request failed (HTTP {status})")


class AuthenticationError(APIError):
    pass


class ChallengeRequired(AuthenticationError):
    def __init__(self, method=None):
        self.method = method
        super().__init__(403, "challenge_required")


class AccessDenied(APIError):
    """Cloudflare's firewall refused the request outright (error 1020).

    Distinct from `ChallengeRequired`: a challenge can be completed, a deny rule cannot.
    Retrying or opening a browser only extends the block, so the SDK raises this instead
    of retrying. The remedy is a different egress (proxy, mobile network) or waiting.
    """

    def __init__(self, detail="firewall deny"):
        self.detail = detail
        super().__init__(403, "access_denied")

    def __str__(self):
        return (
            "Bynix refused this IP (Cloudflare firewall deny, error 1020); "
            "retrying will not help — use another network or wait for the rule to expire"
        )


class RateLimitError(APIError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__(429)


class ProtocolError(BynixError):
    pass


class CapabilityError(BynixError):
    pass


class StreamClosed(BynixError):
    pass


class StreamOverflow(BynixError):
    """A slow consumer lost data. Resync history before continuing."""


class OrderOutcomeUnknown(BynixError):
    def __init__(self, client_ref):
        self.client_ref = client_ref
        super().__init__(f"Order outcome unknown; reconcile client_ref={client_ref}")


class DuplicateOrderReference(BynixError):
    def __init__(self, client_ref):
        self.client_ref = client_ref
        super().__init__(f"Order reference already recorded; reconcile client_ref={client_ref}")


class OrderJournalError(BynixError):
    """The order was accepted, but local persistence failed. Never resubmit it."""

    def __init__(self, order):
        self.order = order
        super().__init__(
            "Order accepted but journal persistence failed; preserve the receipt and do not resubmit"
        )


class SessionExpired(AuthenticationError):
    """A supplied session is invalid; credentials may still be valid."""


class ClientClosed(BynixError):
    pass
