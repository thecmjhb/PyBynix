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
