"""Reusable segment labels and explicit Bluetooth print sessions."""
from .renderer import LabelConfig, LabelMode, render_label, format_id, normalize_name
from .models import MediaConfig, PaperType, PrinterCapabilities, PrintResult
from .printer import Printer
from .sync import SyncPrinter
from .warnings import InvalidNameWarning, NameTruncatedWarning, UnsupportedNameCharactersWarning
from .backends import TiMiniBackend, PrinterBackend
from .errors import (PrinterError, BackendUnavailable, UnsupportedFeature,
                     ConnectionStateError, PrintTransmissionError, DeviceStatusError)

__version__ = "1.2.0"
__all__ = ["UnsupportedNameCharactersWarning", "InvalidNameWarning", "NameTruncatedWarning", "LabelConfig", "LabelMode", "render_label", "format_id", "normalize_name", "MediaConfig",
           "PaperType", "PrinterCapabilities", "PrintResult", "Printer", "SyncPrinter",
           "TiMiniBackend", "PrinterBackend", "PrinterError", "BackendUnavailable",
           "UnsupportedFeature", "ConnectionStateError", "PrintTransmissionError", "DeviceStatusError"]

from .label_printer import (LabelPrinter, LabelSettings, LabelState, RemovalConfirmation,
                     RemovalRequired, LabelOperationError)
__all__ += ["LabelPrinter", "LabelSettings", "LabelState", "RemovalConfirmation",
            "RemovalRequired", "LabelOperationError"]


