"""Public warning categories for recoverable label adjustments."""


class NameTruncatedWarning(UserWarning):
    """The name was shortened to fit three rows of eleven characters."""


class InvalidNameWarning(UserWarning):
    """A missing or non-string name was omitted from the label."""
