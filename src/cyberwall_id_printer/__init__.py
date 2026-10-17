"""Reusable segment labels and explicit Bluetooth print sessions."""
from .renderer import LabelConfig, LabelMode, render_label, format_id, normalize_name
from .models import MediaConfig, PaperType, PrinterCapabilities, PrintResult
from .printer import Printer
from .sync import SyncPrinter
from .backends import TiMiniBackend, PrinterBackend
from .errors import (PrinterError, BackendUnavailable, UnsupportedFeature,
                     ConnectionStateError, PrintTransmissionError)

__version__ = "0.4.1"
__all__ = ["LabelConfig", "LabelMode", "render_label", "format_id", "normalize_name", "MediaConfig",
           "PaperType", "PrinterCapabilities", "PrintResult", "Printer", "SyncPrinter",
           "TiMiniBackend", "PrinterBackend", "PrinterError", "BackendUnavailable",
           "UnsupportedFeature", "ConnectionStateError", "PrintTransmissionError"]

from .labels import (LabelPrinter, LabelSettings, LabelState, RemovalConfirmation,
                     RemovalRequired, LabelOperationError)
__all__ += ["LabelPrinter", "LabelSettings", "LabelState", "RemovalConfirmation",
            "RemovalRequired", "LabelOperationError"]
