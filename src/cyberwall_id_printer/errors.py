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



class DeviceStatusError(PrinterError):
    """A reported device fault, independent of transport and label position."""
    def __init__(self, reason, status):
        self.reason = reason
        self.status = status
        super().__init__(f'Printer not ready: {reason} ({status})')
