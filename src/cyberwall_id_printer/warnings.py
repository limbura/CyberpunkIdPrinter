"""Public warning categories for recoverable label adjustments."""


class NameTruncatedWarning(UserWarning):
    """The name was shortened to fit three rows of eleven characters."""


class InvalidNameWarning(UserWarning):
    """A missing, non-string or whitespace-only name was omitted from the label."""


class UnsupportedNameCharactersWarning(UserWarning):
    """Unsupported name characters were replaced with underscores."""
