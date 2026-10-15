"""Reusable segment labels and explicit Bluetooth print sessions."""
from .renderer import LabelConfig, render_label, format_id, normalize_name
from .models import MediaConfig, PaperType, PrinterCapabilities, PrintResult
from .printer import Printer
from .sync import SyncPrinter
from .backends import TiMiniBackend, PrinterBackend
from .errors import (PrinterError, BackendUnavailable, UnsupportedFeature,
                     ConnectionStateError, PrintTransmissionError)

__version__ = "0.1.0"
__all__ = ["LabelConfig", "render_label", "format_id", "normalize_name", "MediaConfig",
           "PaperType", "PrinterCapabilities", "PrintResult", "Printer", "SyncPrinter",
           "TiMiniBackend", "PrinterBackend", "PrinterError", "BackendUnavailable",
           "UnsupportedFeature", "ConnectionStateError", "PrintTransmissionError"]
