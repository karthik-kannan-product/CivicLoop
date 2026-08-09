class IdentityError(Exception):
    """Base class for redacted administrator identity failures."""


class IdentityCryptoError(IdentityError):
    """Raised when credential encryption or key loading fails closed."""

    def __init__(self) -> None:
        super().__init__("Identity credential protection is unavailable.")


class IdentityUnavailable(IdentityError):
    def __init__(self) -> None:
        super().__init__("Administrator authentication is temporarily unavailable.")


class IdentityRateLimited(IdentityError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Too many verification attempts.")
