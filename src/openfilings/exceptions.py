"""Domain exceptions exposed by OpenFilings."""


class OpenFilingsError(Exception):
    """Base exception for expected OpenFilings failures."""


class ConfigurationError(OpenFilingsError):
    """Raised when required local configuration is missing or invalid."""


class SourceError(OpenFilingsError):
    """Raised when an upstream filing source cannot satisfy a request."""


class FilingNotFoundError(OpenFilingsError):
    """Raised when a filing ID cannot be resolved."""


class DocumentUnavailableError(OpenFilingsError):
    """Raised when a filing has no usable source document."""


class ExtractionError(OpenFilingsError):
    """Raised when a source document cannot be converted."""


class FinancialsUnavailableError(OpenFilingsError):
    """Raised when a filing has no usable tagged financial data."""
