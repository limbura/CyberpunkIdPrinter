"""Описывает общие данные: параметры бумаги, возможности принтера и результат отправки задания."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import isfinite

class PaperType(str, Enum):
    """Тип бумаги"""
    CONTINUOUS = "continuous"
    LABELS = "labels"


@dataclass(frozen=True)
class MediaConfig:
    """Параметры печати"""
    paper_type: PaperType = PaperType.CONTINUOUS	# Тип бумаги
    width_mm: float = 40.0							# Ширина печатываемого изображения (мм)
    height_mm: float = 30.0							# Высота печатываемого изображения (мм)
    roll_width_mm: float = 57.0						# Полная ширина ленты включая подложку
    offset_x_mm: float = 0.0						# Смещение изображения относительно центра принтера, -5.0 = влево на 5мм
    dpi: float = 203.2								# Разрешение печати в точках на дюйм

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
    """Параметры принтера"""
    name: str						# Название принтера
    address: str					# Bluetooth адрес
    profile: str					# Профиль протокола принтера TiMini, например v5g_small_203
    head_width_px: int				# Ширина печати печатающей головки
    nominal_dpi: float				# Разрешение печатающей головки
    continuous: bool = True			# Поддержка печати на непрерывной ленте
    hardware_labels: bool = False	# Поддержка аппаратной работы с отдельными стикерами и их границами
    calibration: bool = False		# Поддержка команды калибровки положения стикеров
    label_note: str = "Label boundary commands are not implemented for this profile."  # Пояснения о возможностях принтера


@dataclass(frozen=True)
class PrintResult:
    """Результаты печати"""
    width_px: int								# Ширина отправленного на печать изображения
    height_px: int								# Высота отправленного на печать изображения
    status: str = "sent"						# Статус задания
    physical_completion_confirmed: bool = False	# Принтер подтвердил физическое завершение выполнения задания
    copies: int = 1								# Количество экземпляров в задании


def validate_copies(copies: int) -> None:
    """Проверка что заданное количество копий - целое число от 1 до 100"""
    if type(copies) is not int or not 1 <= copies <= 100:
        raise ValueError("copies must be an integer from 1 to 100")
