def hex_to_rgb(hex_str):
    """
    Преобразует HEX-строку вида '#FF5733' в кортеж (R, G, B) как целые числа 0-255.
    Поддерживает форматы: '#RRGGBB', 'RRGGBB'
    """
    hex_str = hex_str.lstrip("#")
    if len(hex_str) != 6:
        raise ValueError(f"Некорректный HEX-цвет: {hex_str}")
    return tuple(int(hex_str[i: i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r, g, b):
    """
    Преобразует RGB (0-255) в HEX-строку вида 'FF5733'.
    """
    return f"{r:02X}{g:02X}{b:02X}"


def hex_to_rgb_float(hex_str):
    """
    Преобразует HEX в RGB как float значения от 0.0 до 1.0 (как в ESPHome).
    """
    r, g, b = hex_to_rgb(hex_str)
    return r / 255.0, g / 255.0, b / 255.0


def rgb_float_to_hex(r, g, b):
    """
    Преобразует RGB float (0.0–1.0) в HEX строку.
    """
    return rgb_to_hex(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
