from PIL import Image
from .models import MediaConfig
from .renderer import LabelConfig, render_label


def render_for_head(account_id: str, name: str, media: MediaConfig,
                    head_width_px: int, *, inactive: bool = True,
                    decorations: bool = True) -> Image.Image:
    label = render_label(account_id, name, LabelConfig(
        media.width_mm, media.height_mm, media.dpi, inactive, decorations))
    left = (head_width_px - label.width) // 2 + round(media.offset_x_mm * media.dpi / 25.4)
    if left < 0 or left + label.width > head_width_px:
        raise ValueError("Design does not fit the print head at this width/offset; no cropping applied")
    canvas = Image.new("1", (head_width_px, label.height), 1)
    canvas.paste(label, (left, 0))
    return canvas
