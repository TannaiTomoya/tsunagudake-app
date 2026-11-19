import re
from pathlib import Path

import pytest


def luminance(value: str) -> float:
    channels = (int(value[n : n + 2], 16) / 255 for n in (1, 3, 5))
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return sum(x * y for x, y in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        ("text", "canvas"),
        ("text", "surface"),
        ("muted", "surface"),
        ("muted", "rail"),
        ("muted", "raised"),
        ("on-action", "action"),
        ("action", "surface"),
        ("seal", "surface"),
        ("error", "surface"),
    ],
)
def test_t31_theme_contrast(foreground: str, background: str) -> None:
    css = Path("app/static/css/theme.css").read_text()
    palette = dict(re.findall(r"--([a-z-]+):\s*(#[\da-f]{6});", css))
    low, high = sorted(luminance(palette[name]) for name in (foreground, background))
    assert (high + 0.05) / (low + 0.05) >= 4.5
