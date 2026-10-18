"""Размещает нарисованную наклейку на изображении нужной ширины для печатающей головки."""

from PIL import Image
from .models import MediaConfig
from .renderer import LabelConfig, LabelMode, render_label


def render_for_head(account_id: str | None, name: str | None, media: MediaConfig,
                    head_width_px: int, *, inactive: bool = True,
                    decorations: bool = True, mode: LabelMode = LabelMode.ID_NAME) -> Image.Image:
    """Cоздаёт белое изображение шириной head_width_px и помещает на него наклейку со смещением MediaConfig.offset_x_mm"""
    label = render_label(account_id, name, LabelConfig(
        media.width_mm, media.height_mm, media.dpi, inactive, decorations, mode))
    left = (head_width_px - label.width) // 2 + round(media.offset_x_mm * media.dpi / 25.4)
    if left < 0 or left + label.width > head_width_px:
        raise ValueError("Design does not fit the print head at this width/offset; no cropping applied")
    canvas = Image.new("1", (head_width_px, label.height), 1)
    canvas.paste(label, (left, 0))
    return canvas
