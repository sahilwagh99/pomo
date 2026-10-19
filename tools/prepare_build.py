"""Prepare build inputs: Pomo's .exe icon, the installer's wizard images, and the Windows version resource.

Prints the app version (and nothing else) so build.bat can pass it to the installer.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402

from pomodoro import APP_NAME, APP_VERSION  # noqa: E402
from pomodoro.icons import render_pomo_icon, write_ico  # noqa: E402
from pomodoro.pomo import Pomo  # noqa: E402
from pomodoro.theme import LIGHT  # noqa: E402

app = QGuiApplication([])
write_ico(ROOT / "assets" / "pomo.ico")
render_pomo_icon(256).save(str(ROOT / "docs" / "icon.png"))

# Installer wizard images (100% and 200% scale), Pomo on Matcha cream
wizard = ROOT / "build" / "wizard"
wizard.mkdir(parents=True, exist_ok=True)


def wizard_image(width: int, height: int, scale: int, large: bool) -> QImage:
    w, h = width * scale, height * scale
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(LIGHT.page if large else "#ffffff"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    if large:
        Pomo().paint(p, QPointF(w / 2, h * 0.42), w * 0.68)
        font = QFont("Segoe UI")
        font.setPixelSize(int(26 * scale))
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(LIGHT.ink))
        p.drawText(QRectF(0, h * 0.62, w, 40 * scale), Qt.AlignHCenter | Qt.AlignTop, APP_NAME)
        font.setPixelSize(int(11 * scale))
        font.setWeight(QFont.Normal)
        p.setFont(font)
        p.setPen(QColor(LIGHT.ink_2))
        p.drawText(QRectF(0, h * 0.62 + 38 * scale, w, 40 * scale), Qt.AlignHCenter | Qt.AlignTop,
                   "your focus buddy")
    else:
        p.drawImage(QRectF(w * 0.04, h * 0.04, w * 0.92, h * 0.92), render_pomo_icon(w).toImage())
    p.end()
    return img


for scale in (1, 2):
    wizard_image(164, 314, scale, large=True).save(str(wizard / f"large-{scale}x.bmp"), "BMP")
    wizard_image(55, 55, scale, large=False).save(str(wizard / f"small-{scale}x.bmp"), "BMP")

parts = [int(p) for p in APP_VERSION.split(".")][:4]
quad = tuple(parts + [0] * (4 - len(parts)))
version_info = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={quad}, prodvers={quad}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{APP_NAME}'),
      StringStruct('FileDescription', '{APP_NAME} focus timer'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
out = ROOT / "build" / "version_info.txt"
out.write_text(version_info, encoding="utf-8")
print(APP_VERSION)
