from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import isfinite


class PaperType(str, Enum):
    CONTINUOUS = "continuous"
    LABELS = "labels"


@dataclass(frozen=True)
class MediaConfig:
    """Layout geometry, not a command to resize a printer's physical paper.

    width/height describe one design; roll_width includes the liner. Offset is
    relative to the head centre, positive to the right. Hardware label mode
    requires a supported sensor recipe, never simulated feed distances.
    """
    paper_type: PaperType = PaperType.CONTINUOUS
    width_mm: float = 40.0
    height_mm: float = 30.0
    roll_width_mm: float = 57.0
    offset_x_mm: float = 0.0
    dpi: float = 203.2

    def __post_init__(self):
        object.__setattr__(self, "paper_type", PaperType(self.paper_type))
        for name in ("width_mm", "height_mm", "roll_width_mm", "dpi"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not isfinite(self.offset_x_mm):
            raise ValueError("offset_x_mm must be finite")
        if self.width_mm > self.roll_width_mm:
            raise ValueError("Design width exceeds roll/liner width")


@dataclass(frozen=True)
class PrinterCapabilities:
    name: str
    address: str
    profile: str
    head_width_px: int
    nominal_dpi: float
    continuous: bool = True
    hardware_labels: bool = False
    calibration: bool = False
    label_note: str = "Label boundary commands are not implemented for this profile."


@dataclass(frozen=True)
class PrintResult:
    """Transmission success; does not confirm a physical sticker was printed."""
    width_px: int
    height_px: int
    status: str = "sent"
    physical_completion_confirmed: bool = False
    copies: int = 1


def validate_copies(copies: int) -> None:
    if type(copies) is not int or not 1 <= copies <= 100:
        raise ValueError("copies must be an integer from 1 to 100")
