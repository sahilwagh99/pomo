"""Prepare build inputs: the .exe icon and the Windows version resource.

Prints the app version (and nothing else) so build.bat can pass it to the installer.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402

from pomodoro import APP_NAME, APP_VERSION  # noqa: E402
from pomodoro.icons import write_ico  # noqa: E402
from pomodoro.theme import DARK  # noqa: E402

app = QGuiApplication([])
accent = QColor(DARK.focus)
track = QColor(accent)
track.setAlpha(70)
write_ico(ROOT / "assets" / "focusorb.ico", accent, QColor(DARK.orb_face), track)

parts = [int(p) for p in APP_VERSION.split(".")][:4]
quad = tuple(parts + [0] * (4 - len(parts)))
version_info = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={quad}, prodvers={quad}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{APP_NAME}'),
      StringStruct('FileDescription', '{APP_NAME} Pomodoro timer'),
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
out.parent.mkdir(exist_ok=True)
out.write_text(version_info, encoding="utf-8")
print(APP_VERSION)
