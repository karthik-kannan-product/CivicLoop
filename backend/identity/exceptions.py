class IdentityError(Exception):
    """Base class for redacted administrator identity failures."""


class IdentityCryptoError(IdentityError):
    """Raised when credential encryption or key loading fails closed."""

    def __init__(self) -> None:
        super().__init__("Identity credential protection is unavailable.")
