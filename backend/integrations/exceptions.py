class IntegrationError(Exception):
    """Base class for redacted integration failures."""


class IntegrationCryptoError(IntegrationError):
    """Raised when integration credential protection is unavailable."""

    def __init__(self) -> None:
        super().__init__("Integration credential protection is unavailable.")
