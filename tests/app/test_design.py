import re
from pathlib import Path

import pytest

from app.charts import LINE_HEIGHT, LINE_WIDTH, bars, segments, trend_line


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


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        ("line", "surface"),
        ("line", "canvas"),
        ("line", "raised"),
        ("action", "canvas"),
        ("action", "rail"),
        ("on-action", "action-hover"),
    ],
)
def test_t31_borders_and_focus_stand_out(foreground: str, background: str) -> None:
    # 入力欄の境界とフォーカスの枠は、隣の色と3:1以上（#93）。
    css = Path("app/static/css/theme.css").read_text()
    palette = dict(re.findall(r"--([a-z-]+):\s*(#[\da-f]{6});", css))
    low, high = sorted(luminance(palette[name]) for name in (foreground, background))
    assert (high + 0.05) / (low + 0.05) >= 3


def test_light_scheme_is_the_default_for_projection() -> None:
    # 明るい会場で投影しても読めるよう、初期表示は明るい配色（#93）。
    base = Path("app/templates/base.html").read_text()
    theme = Path("app/static/css/theme.css").read_text()
    assert 'data-theme="light"' in base and 'data-theme="dark"' not in base
    color = re.search(r'name="theme-color" content="(#[\da-f]{6})"', base)
    palette = dict(re.findall(r"--([a-z-]+):\s*(#[\da-f]{6});", theme))
    assert color and color.group(1) == palette["canvas"]
    assert ':root[data-theme="light"]' in theme and "color-scheme: light;" in theme
    assert luminance(palette["canvas"]) > 0.8 and luminance(palette["text"]) < 0.05
    # 入力部品のアイコンも、白い地で見える色にする。
    for icon in ("chevron", "calendar"):
        svg = Path(f"app/static/icons/{icon}.svg").read_text()
        stroke = re.search(r'stroke="(#[\da-f]{6})"', svg)
        assert stroke
        low, high = sorted(luminance(c) for c in (stroke.group(1), palette["surface"]))
        assert (high + 0.05) / (low + 0.05) >= 3


def test_kidou_layer_uses_theme_tokens_and_respects_reduced_motion() -> None:
    css = Path("app/static/css/kidou.css").read_text()
    theme = Path("app/static/css/theme.css").read_text()
    assert not re.search(r"#[\da-fA-F]{3,8}\b", css)
    assert "http" not in css and "@import" not in css
    for token in set(re.findall(r"var\((--[a-z-]+)", css)) - {"--corner"}:
        assert f"{token}:" in theme
    outside, guarded = css.split("@media (prefers-reduced-motion: no-preference)")
    assert "animation:" in guarded
    assert not re.search(r"animation:(?!\s*none)", outside)
    # 飾りの色を文字に使うなら、疑似要素の飾りだけ（今は使っていない）。
    for line in css.splitlines():
        if re.search(r"(?<![-\w])color: var\(--(misreg|volt)", line):
            assert "::before" in line or "::after" in line


def test_top_heading_and_figure_stay_still_and_single() -> None:
    # トップの見出しは最初から単色・一重。説明文の後ろに面や線を重ねない（#95）。
    index = Path("app/templates/public/index.html").read_text()
    kidou = Path("app/static/css/kidou.css").read_text()
    theme = Path("app/static/css/theme.css").read_text()
    figure = Path("app/templates/public/ledger-figure.svg").read_text()
    assert "data-text" not in index
    for gone in (".hero::before", ".hero::after", "mix-blend-mode"):
        assert gone not in kidou and gone not in theme
    assert "repeating-linear-gradient" not in kidou
    for gone in ("kidou-band", "kidou-title", "kidou-boot", "misreg-settle"):
        assert gone not in kidou and gone not in theme
    # 見出し（h1・h2）の規則に、文字の影を付けない。
    for css in (kidou, theme):
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(r"\bh[12]\b", selector):
                assert "text-shadow" not in body, selector.strip()
    # 帳簿図は主線を一組だけ。ずれた色の線を引かない。
    assert "misreg" not in figure


def test_t49_chart_geometry_stays_inside_the_frame() -> None:
    drawn = trend_line([0, None, 5, 20, None, 12])
    assert drawn.gaps == (drawn.ticks[1], drawn.ticks[4])
    # 伏せた月でとぎれる。1点だけの所は、線を引かない。
    assert len(drawn.paths) == 1 and drawn.paths[0].count(",") == 2
    assert len(drawn.points) == 4
    assert all(0 < x < LINE_WIDTH and 0 < y < LINE_HEIGHT for x, y in drawn.points)
    assert [bar.percent for bar in bars("abc", [None, 5, 20])] == [0.0, 25.0, 100.0]
    assert all(bar.percent == 0 for bar in bars("ab", [0, 0]))
    parts = segments(("a", "b", "c"), (5, 0, 15))
    assert [(part.start, part.width) for part in parts] == [
        (0.0, 25.0),
        (25.0, 0.0),
        (25.0, 75.0),
    ]
    assert [part.pattern for part in parts] == ["solid", "hatch", "dots"]


def test_t49_charts_use_no_accent_colours() -> None:
    css = Path("app/static/css/kidou.css").read_text()
    charts = css.split("地域のグラフ")[1].split("/* ")[0]
    assert ".chart-solid" in charts
    assert not re.search(r"var\(--(action|seal|misreg|volt|error)", charts)
