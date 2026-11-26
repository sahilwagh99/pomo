"""Builds Pomo's Microsoft Store package (MSIX) from the PyInstaller build in dist\\Pomo (run build.bat first).

  dist\\msix\\       the package layout: the app, AppxManifest.xml and the tile images. With Developer Mode
                    on, `Add-AppxPackage -Register dist\\msix\\AppxManifest.xml` installs it from there to
                    try it out, and `Get-AppxPackage *Pomo* | Remove-AppxPackage` takes it away again.
  dist\\Pomo.msix   the package to upload to Partner Center, made when makeappx.exe (Windows SDK) is found.
                    The Store signs it after certification, so it is left unsigned.

The identity has to match Partner Center's Product identity page once Pomo's name is reserved:
  POMO_STORE_NAME            Package/Identity/Name, like 12345YourName.Pomo
  POMO_STORE_PUBLISHER       Package/Identity/Publisher, like CN=1A2B3C4D-5E6F-...
  POMO_STORE_PUBLISHER_NAME  Package/Properties/PublisherDisplayName
  POMO_STORE_DISPLAY_NAME    the name you reserved (default Pomo)
Without them it builds a package for local testing, and says so. With --store they're required, and so
is makeappx.exe (set POMO_MAKEAPPX if it isn't found).
"""
from __future__ import annotations

import os
import re
import shutil
import string
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pomodoro import APP_NAME, APP_VERSION  # noqa: E402
from sign import find_sdk_tool  # noqa: E402

APP = ROOT / "dist" / "Pomo"
LAYOUT = ROOT / "dist" / "msix"
PACKAGE = ROOT / "dist" / "Pomo.msix"
TEMPLATE = ROOT / "installer" / "AppxManifest.xml"
DESCRIPTION = "A cozy Pomodoro timer with a focus buddy, a study coach, habits and an eye on your coding agents."
LOCAL_IDENTITY = {"name": "Pomo.LocalTest", "publisher": "CN=Pomo Local Test", "publisher_name": "Pomo (local test)"}
# Tile images: file name -> (width, height, Pomo's share of the height)
TILES = {
    "StoreLogo.png": (50, 50, 1.0),
    "Square44x44Logo.png": (44, 44, 1.0),
    "Square150x150Logo.png": (150, 150, 0.66),
    "Wide310x150Logo.png": (310, 150, 0.66),
}


def package_version(version: str = APP_VERSION) -> str:
    """MSIX wants four numbers, and the Store wants the last one to be 0: 1.9.0 -> 1.9.0.0."""
    parts = re.findall(r"\d+", version)[:3]
    return ".".join((parts + ["0"] * 3)[:3] + ["0"])


def identity(env: dict[str, str], store: bool) -> dict[str, str]:
    values = {"name": env.get("POMO_STORE_NAME", "").strip(),
              "publisher": env.get("POMO_STORE_PUBLISHER", "").strip(),
              "publisher_name": env.get("POMO_STORE_PUBLISHER_NAME", "").strip()}
    missing = [key for key, value in values.items() if not value]
    if missing and store:
        raise SystemExit("--store needs POMO_STORE_NAME, POMO_STORE_PUBLISHER and POMO_STORE_PUBLISHER_NAME "
                         "(Partner Center > your app > Product identity).")
    if missing:
        print("WARNING: no Partner Center identity set, so this is a local test package, not one for the Store.")
        return dict(LOCAL_IDENTITY)
    return values


def manifest(values: dict[str, str]) -> str:
    fields = {"version": package_version(), "description": DESCRIPTION, "display_name": APP_NAME, **values}
    attribute = {key: escape(value, {'"': "&quot;"}) for key, value in fields.items()}
    return string.Template(TEMPLATE.read_text(encoding="utf-8")).substitute(attribute)


def write_tiles(folder: Path) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    from pomodoro.icons import render_pomo_icon

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - painting needs one
    folder.mkdir(parents=True, exist_ok=True)
    for name, (width, height, share) in TILES.items():
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        size = round(height * share)
        painter = QPainter(image)
        painter.drawPixmap(QPoint((width - size) // 2, (height - size) // 2), render_pomo_icon(size))
        painter.end()
        image.save(str(folder / name))


def main(argv: list[str]) -> int:
    store = "--store" in argv
    if not (APP / "Pomo.exe").is_file():
        print(f"ERROR: {APP} has no Pomo.exe yet: run build.bat first.")
        return 1
    values = identity(dict(os.environ), store)
    values.setdefault("display_name", os.environ.get("POMO_STORE_DISPLAY_NAME", "").strip() or APP_NAME)

    if LAYOUT.exists():
        shutil.rmtree(LAYOUT)
    shutil.copytree(APP, LAYOUT)
    write_tiles(LAYOUT / "Assets")
    (LAYOUT / "AppxManifest.xml").write_text(manifest(values), encoding="utf-8")
    print(f"Package layout: {LAYOUT} (version {package_version()}, identity {values['name']})")

    makeappx = find_sdk_tool("makeappx.exe", "POMO_MAKEAPPX")
    if not makeappx:
        if store:
            print("ERROR: makeappx.exe was not found: install the Windows SDK, or set POMO_MAKEAPPX to it.")
            return 1
        print("makeappx.exe (Windows SDK) not found, so no .msix was made. The layout can still be tried out "
              "in Developer Mode: Add-AppxPackage -Register dist\\msix\\AppxManifest.xml")
        return 0
    result = subprocess.run([makeappx, "pack", "/d", str(LAYOUT), "/p", str(PACKAGE), "/o"])
    if result.returncode != 0:
        print("ERROR: makeappx could not pack the layout.")
        return 1
    print(f"Package: {PACKAGE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
