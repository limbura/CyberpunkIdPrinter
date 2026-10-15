from cyberwall_id_printer import LabelConfig, render_label

if __name__ == "__main__":
    config = LabelConfig(width_mm=40, height_mm=30)
    image = render_label("123456789", "Роза фон Праунхайм", config)
    image.save("label.png", dpi=(config.dpi, config.dpi))
