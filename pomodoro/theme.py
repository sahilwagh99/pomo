"""Design tokens.

Every surface, ink and chart color comes from a Theme, so the two themes swap in
one place: "Matcha" (light, warm cream and cocoa ink) and "Cocoa" (dark, warm
brown, never black). Mode colors are soft pastels for the UI (matcha, sakura,
lavender); they always sit next to a text label, so color never carries meaning
alone. Charts use a deeper matcha (`chart`) checked to stay readable on the
card surface, and the heatmap uses a single-hue matcha ramp.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen, QPixmap

FONT_FAMILY = "Segoe UI"


@dataclass(frozen=True)
class Theme:
    key: str
    dark: bool
    page: str
    surface: str
    raised: str
    hover: str
    orb_face: str
    orb_edge: str
    ink: str
    ink_2: str
    muted: str
    grid: str
    axis: str
    border_rgba: tuple[int, int, int, int]
    success: str
    danger: str
    on_accent: str
    focus: str
    short: str
    long: str
    chart: str             # data marks: deeper than the pastel accents, >= 3:1 on surface
    heat: tuple[str, ...]  # low -> high magnitude
    heat_empty: str
    shadow_alpha: int
    label: str = ""

    def q(self, token: str) -> QColor:
        return QColor(getattr(self, token))

    def shadow(self, alpha: int) -> QColor:
        """Warm shadow tone (cocoa on Matcha, near-black brown on Cocoa)."""
        r, g, b = (12, 8, 7) if self.dark else (63, 42, 38)
        return QColor(r, g, b, alpha)

    @property
    def border(self) -> QColor:
        return QColor(*self.border_rgba)

    @property
    def border_qss(self) -> str:
        r, g, b, a = self.border_rgba
        return f"rgba({r},{g},{b},{a})"


LIGHT = Theme(  # "Matcha"
    key="light",
    dark=False,
    page="#f8f3ea",
    surface="#fffdf8",
    raised="#f3ede2",
    hover="#ebe3d5",
    orb_face="#fffdf8",
    orb_edge="#e3d8c6",
    ink="#3f2a26",
    ink_2="#6e5a54",
    muted="#9b8a82",
    grid="#efe7da",
    axis="#ddd0bf",
    border_rgba=(63, 42, 38, 30),
    success="#4f7f43",
    danger="#b5544e",
    on_accent="#3f2a26",
    focus="#95c283",
    short="#f0a3b1",
    long="#a7b3e3",
    chart="#6f9d60",
    heat=("#e4efd9", "#c6dfb4", "#a3c98d", "#7fb068", "#5f8f50"),
    heat_empty="#f1eadf",
    shadow_alpha=40,
    label="Matcha (light)",
)

DARK = Theme(  # "Cocoa"
    key="dark",
    dark=True,
    page="#251e1c",
    surface="#2f2623",
    raised="#3a2f2b",
    hover="#453833",
    orb_face="#342a27",
    orb_edge="#4b3d37",
    ink="#f6ede3",
    ink_2="#d2c3b6",
    muted="#a39188",
    grid="#40342f",
    axis="#54453e",
    border_rgba=(246, 237, 227, 26),
    success="#a9cf8f",
    danger="#ec9b94",
    on_accent="#2b211f",
    focus="#a6cf92",
    short="#f2abb8",
    long="#b0bbe8",
    chart="#6fa45a",
    heat=("#3d4a33", "#4f6541", "#67844f", "#86a96a", "#a9cb8e"),
    heat_empty="#3d322e",
    shadow_alpha=110,
    label="Cocoa (dark)",
)

SAKURA = Theme(
    key="sakura",
    dark=False,
    page="#fbf1f1",
    surface="#fffafa",
    raised="#f8e9ea",
    hover="#f2dcdf",
    orb_face="#fffafa",
    orb_edge="#efd6da",
    ink="#4a2a33",
    ink_2="#7a5560",
    muted="#a68b92",
    grid="#f3e3e5",
    axis="#e3cbd0",
    border_rgba=(74, 42, 51, 30),
    success="#4f7f43",
    danger="#b5544e",
    on_accent="#4a2a33",
    focus="#eea4b4",
    short="#a8d5b0",
    long="#c3b3e6",
    chart="#c96b80",
    heat=("#fbe1e6", "#f5c3ce", "#eb9fb0", "#dc7f95", "#c4607a"),
    heat_empty="#f5e8ea",
    shadow_alpha=40,
    label="Sakura (light)",
)

LAVENDER = Theme(
    key="lavender",
    dark=False,
    page="#f4f2fa",
    surface="#fdfcff",
    raised="#efecf7",
    hover="#e6e1f2",
    orb_face="#fdfcff",
    orb_edge="#e0daf0",
    ink="#352f4a",
    ink_2="#5f5878",
    muted="#928ba8",
    grid="#ece8f5",
    axis="#d9d2ea",
    border_rgba=(53, 47, 74, 30),
    success="#4f7f43",
    danger="#b5544e",
    on_accent="#352f4a",
    focus="#b3a4e6",
    short="#f3b7c6",
    long="#9fd0d9",
    chart="#7b68c8",
    heat=("#ebe6fa", "#d4caf3", "#b8a8ea", "#9a87dc", "#7b68c8"),
    heat_empty="#efecf6",
    shadow_alpha=40,
    label="Lavender (light)",
)

BLUEBERRY = Theme(
    key="blueberry",
    dark=True,
    page="#1e2130",
    surface="#262a3b",
    raised="#2f3447",
    hover="#383e54",
    orb_face="#2a2f42",
    orb_edge="#3d4459",
    ink="#eef0f8",
    ink_2="#c4c8da",
    muted="#8f95ad",
    grid="#343a4e",
    axis="#454c63",
    border_rgba=(238, 240, 248, 26),
    success="#a9cf8f",
    danger="#ec9b94",
    on_accent="#1e2130",
    focus="#9fb8ef",
    short="#f2b6c6",
    long="#c4b5ef",
    chart="#6a8ee0",
    heat=("#2f3a5c", "#3c5084", "#4d68ad", "#6a8ee0", "#a5bdf2"),
    heat_empty="#30364a",
    shadow_alpha=110,
    label="Blueberry (dark)",
)

PEACH = Theme(
    key="peach",
    dark=False,
    page="#fbf3ec",
    surface="#fffbf7",
    raised="#f7ebe0",
    hover="#f0dfd0",
    orb_face="#fffbf7",
    orb_edge="#f0dccb",
    ink="#4a2f24",
    ink_2="#7a5a4c",
    muted="#a88e82",
    grid="#f4e7db",
    axis="#e6d2c1",
    border_rgba=(74, 47, 36, 30),
    success="#4f7f43",
    danger="#b5544e",
    on_accent="#4a2f24",
    focus="#f4b48c",
    short="#9fd3c7",
    long="#c4b5e6",
    chart="#c2703f",  # validated: L band, chroma, >= 3:1 on surface
    heat=("#e59a6e", "#d4824f", "#bd6c3b", "#a0592f", "#834826"),
    heat_empty="#f6ece3",
    shadow_alpha=40,
    label="Peach (light)",
)

MINT = Theme(
    key="mint",
    dark=False,
    page="#eef7f3",
    surface="#fbfffd",
    raised="#e6f2ec",
    hover="#d9ebe2",
    orb_face="#fbfffd",
    orb_edge="#d5e9df",
    ink="#22403a",
    ink_2="#4d6b64",
    muted="#869f98",
    grid="#e3efe9",
    axis="#cde0d7",
    border_rgba=(34, 64, 58, 30),
    success="#3f7f4f",
    danger="#b5544e",
    on_accent="#22403a",
    focus="#8fd4bf",
    short="#f4b6c2",
    long="#b9c3ec",
    chart="#1f9b7e",
    heat=("#5ec0a1", "#3aab8b", "#1f9478", "#177d65", "#106653"),
    heat_empty="#e8f2ed",
    shadow_alpha=40,
    label="Mint (light)",
)

PLUM = Theme(
    key="plum",
    dark=True,
    page="#221b29",
    surface="#2e2536",
    raised="#382d41",
    hover="#43364d",
    orb_face="#30273a",
    orb_edge="#4a3d56",
    ink="#f3ecf8",
    ink_2="#d0c3dc",
    muted="#9e90ab",
    grid="#3a2f44",
    axis="#4d4058",
    border_rgba=(243, 236, 248, 26),
    success="#a9cf8f",
    danger="#ec9b94",
    on_accent="#221b29",
    focus="#c3a5ec",
    short="#f2b6c6",
    long="#9fd3c7",
    chart="#a07ad8",
    heat=("#6a4f9b", "#8364ba", "#9b7bd2", "#b495e4", "#cfb6f3"),
    heat_empty="#372d40",
    shadow_alpha=110,
    label="Plum (dark)",
)

THEMES = {t.key: t for t in (LIGHT, SAKURA, LAVENDER, PEACH, MINT, DARK, BLUEBERRY, PLUM)}


def theme_for(key: str) -> Theme:
    return THEMES.get(key, LIGHT)


def chart_color(theme: Theme, settings) -> QColor:
    """Color for focus data in charts: the user's focus color if they picked one."""
    override = getattr(settings, "focus_color", "")
    if override and QColor.isValidColorName(override):
        return QColor(override)
    return QColor(theme.chart)


def mode_color(theme: Theme, settings, mode: str) -> QColor:
    """Theme color for a mode ("focus" / "short" / "long"), unless the user picked one."""
    override = getattr(settings, f"{mode}_color", "")
    if override and QColor.isValidColorName(override):
        return QColor(override)
    return QColor(getattr(theme, mode))


def build_palette(theme: Theme, accent: QColor) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.Window: theme.page,
        QPalette.WindowText: theme.ink,
        QPalette.Base: theme.raised,
        QPalette.AlternateBase: theme.surface,
        QPalette.Text: theme.ink,
        QPalette.Button: theme.raised,
        QPalette.ButtonText: theme.ink,
        QPalette.ToolTipBase: theme.surface,
        QPalette.ToolTipText: theme.ink,
        QPalette.PlaceholderText: theme.muted,
        QPalette.BrightText: theme.ink,
        QPalette.Link: theme.long,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Highlight, accent)
    pal.setColor(QPalette.HighlightedText, QColor(theme.on_accent))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(theme.muted))
    return pal


def _render_png(path: Path, size: int, draw) -> None:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    draw(p, size)
    p.end()
    pm.save(str(path), "PNG")


def ensure_qss_assets(theme: Theme, asset_dir: Path) -> dict[str, str]:
    """Small PNGs the stylesheet references (toggle switches, check mark, chevrons)."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "switch_on": asset_dir / f"switch_on_{theme.key}.png",
        "switch_off": asset_dir / f"switch_off_{theme.key}.png",
        "check": asset_dir / f"check_{theme.key}.png",
        "down": asset_dir / f"chev_down_{theme.key}.png",
        "up": asset_dir / f"chev_up_{theme.key}.png",
    }

    def check(p: QPainter, s: int) -> None:
        p.setPen(QPen(QColor(theme.on_accent), s * 0.13, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(QPointF(s * 0.24, s * 0.52))
        path.lineTo(s * 0.42, s * 0.70)
        path.lineTo(s * 0.76, s * 0.32)
        p.drawPath(path)

    def chevron(up: bool):
        def draw(p: QPainter, s: int) -> None:
            p.setPen(QPen(QColor(theme.ink_2), s * 0.12, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            y0, y1 = (0.62, 0.36) if up else (0.38, 0.64)
            path = QPainterPath(QPointF(s * 0.22, s * y0))
            path.lineTo(s * 0.5, s * y1)
            path.lineTo(s * 0.78, s * y0)
            p.drawPath(path)
        return draw

    def switch(on: bool):
        def draw(p: QPainter, w: int, h: int) -> None:
            track = QColor(theme.focus if on else theme.axis)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), (h - 1) / 2, (h - 1) / 2)
            r = h / 2 - h * 0.12
            cx = w - h / 2 if on else h / 2
            p.setBrush(QColor(0, 0, 0, 28))
            p.drawEllipse(QPointF(cx, h / 2 + h * 0.04), r, r)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(cx, h / 2), r, r)
        return draw

    for key, on in (("switch_on", True), ("switch_off", False)):
        for scale, path in ((1, paths[key]), (2, paths[key].with_name(paths[key].stem + "@2x.png"))):
            pm = QPixmap(34 * scale, 20 * scale)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            switch(on)(painter, 34 * scale, 20 * scale)
            painter.end()
            pm.save(str(path), "PNG")
    _render_png(paths["check"], 16, check)
    _render_png(paths["down"], 12, chevron(False))
    _render_png(paths["up"], 12, chevron(True))
    return {k: v.as_posix() for k, v in paths.items()}


def build_qss(theme: Theme, accent: QColor, assets: dict[str, str]) -> str:
    t = theme
    acc = accent.name()
    acc_hover = accent.lighter(112).name()
    return f"""
QWidget#page, QDialog {{ background: {t.page}; color: {t.ink}; }}
QLabel {{ color: {t.ink}; background: transparent; }}
QLabel[role="h1"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="secondary"] {{ color: {t.ink_2}; }}
QLabel[role="muted"] {{ color: {t.muted}; font-size: 12px; }}
QLabel[role="hero"] {{ font-size: 52px; font-weight: 600; }}
QLabel[role="tileValue"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="section"] {{ color: {t.muted}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QFrame#card, QFrame#tile {{ background: {t.surface}; border: 1px solid {t.border_qss}; border-radius: 12px; }}
QFrame#segment {{ background: {t.raised}; border: 1px solid {t.border_qss}; border-radius: 9px; }}

QPushButton {{ background: {t.raised}; color: {t.ink}; border: 1px solid {t.border_qss};
    border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:disabled {{ color: {t.muted}; }}
QPushButton#primary {{ background: {acc}; color: {t.on_accent}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {acc_hover}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {t.ink_2}; }}
QPushButton#ghost:hover {{ background: {t.hover}; color: {t.ink}; }}
QPushButton#danger {{ color: {t.danger}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 7px; padding: 5px 12px; color: {t.ink_2}; }}
QPushButton#seg:hover {{ color: {t.ink}; }}
QPushButton#seg:checked {{ background: {t.surface}; color: {t.ink}; font-weight: 600; }}
QToolButton#close {{ background: transparent; border: none; color: {t.muted}; font-size: 15px; padding: 0 4px; }}
QToolButton#close:hover {{ color: {t.ink}; }}

QMenu {{ background: {t.surface}; border: 1px solid {t.border_qss}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ color: {t.ink}; padding: 6px 28px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.hover}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.grid}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
QMenu::indicator:checked {{ image: url("{assets['check']}"); background: {acc}; border-radius: 4px; }}

QToolTip {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.border_qss};
    border-radius: 6px; padding: 6px 8px; }}

QLineEdit, QSpinBox, QComboBox {{ background: {t.raised}; color: {t.ink}; border: 1px solid {t.border_qss};
    border-radius: 7px; padding: 5px 8px; selection-background-color: {acc}; selection-color: {t.on_accent}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {acc}; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 20px; border: none; background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url("{assets['up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.border_qss};
    selection-background-color: {t.hover}; selection-color: {t.ink}; outline: none; padding: 4px; }}

QCheckBox {{ color: {t.ink}; spacing: 8px; }}
QCheckBox::indicator {{ width: 34px; height: 20px; border: none; background: transparent;
    image: url("{assets['switch_off']}"); }}
QCheckBox::indicator:checked {{ image: url("{assets['switch_on']}"); }}
QCheckBox:disabled {{ color: {t.muted}; }}

QSlider::groove:horizontal {{ height: 4px; background: {t.grid}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.ink}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QTabWidget::pane {{ border: 1px solid {t.border_qss}; border-radius: 10px; background: {t.surface}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {t.ink_2}; padding: 8px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.ink}; border-bottom: 2px solid {acc}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t.ink}; }}

QTableWidget {{ background: {t.surface}; color: {t.ink}; border: none; gridline-color: {t.grid};
    selection-background-color: {t.hover}; selection-color: {t.ink}; outline: none; }}
QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {t.grid}; }}
QHeaderView::section {{ background: {t.surface}; color: {t.muted}; border: none; border-bottom: 1px solid {t.axis};
    padding: 6px 8px; font-weight: 600; font-size: 12px; }}
QTableCornerButton::section {{ background: {t.surface}; border: none; }}

QScrollArea {{ border: none; background: {t.page}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.axis}; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.axis}; border-radius: 4px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QMessageBox, QInputDialog, QColorDialog {{ background: {t.page}; }}
"""
