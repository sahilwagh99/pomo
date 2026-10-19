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

THEMES = {"light": LIGHT, "dark": DARK}


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
    """Small PNGs the stylesheet references (check mark, chevrons)."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    paths = {
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
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {t.axis}; background: {t.raised}; }}
QCheckBox::indicator:hover {{ border-color: {acc}; }}
QCheckBox::indicator:checked {{ background: {acc}; border-color: {acc}; image: url("{assets['check']}"); }}

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
