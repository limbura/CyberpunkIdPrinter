"""Public failures: no automatic retransmission after a transport failure."""

class PrinterError(RuntimeError):
    pass


class BackendUnavailable(PrinterError):
    pass


class UnsupportedFeature(PrinterError):
    pass


class ConnectionStateError(PrinterError):
    pass


class PrintTransmissionError(PrinterError):
    """Some bytes may have reached the printer. Inspect paper before retrying."""
    may_have_printed = True
