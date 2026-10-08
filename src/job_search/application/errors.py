class JobSourceError(RuntimeError):
    """A configured source could not be collected successfully."""


class InvalidSourceConfigurationError(JobSourceError):
    """The provider conclusively rejected a company's source identifier."""
