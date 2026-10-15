"""Render all four modes without Bluetooth. Run from VS Code or the terminal."""
from pathlib import Path
from cyberwall_id_printer import LabelConfig, LabelMode, render_label


def main():
    output = Path(__file__).resolve().parent / 'output'
    output.mkdir(exist_ok=True)
    for mode in LabelMode:
        blank = mode in (LabelMode.BLANK_ID_NAME, LabelMode.BLANK_ID_ONLY)
        image = render_label(
            None if blank else '123456789',
            'Роза фон Праунхайм' if mode == LabelMode.ID_NAME else None,
            LabelConfig(mode=mode),
        )
        path = output / f'{mode.value}.png'
        image.save(path, dpi=(203.2, 203.2))
        print(path)


if __name__ == '__main__':
    main()
