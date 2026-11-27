"""Fetch PresentMon, the frame-rate reader Pomo's gaming mode bundles, into pomodoro/bin.

PresentMon is Intel's open-source (MIT) console tool that reads a game's frames from Windows' own event
tracing, so it works for any game without hooking into it. Pomo starts it only for the game in front, and
only when you may read those events (see telemetry.fps_permission).

The download is pinned: a release whose bytes don't match the known hash is refused, so a swapped or
corrupted file never ends up in the build. Its licence is saved next to it, as the MIT licence asks.

Usage:
  python tools/fetch_presentmon.py           download it if it isn't there (or doesn't match)
  python tools/fetch_presentmon.py --force   download it again
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "pomodoro" / "bin"
EXE = BIN / "PresentMon.exe"
LICENSE = BIN / "PresentMon-LICENSE.txt"

VERSION = "2.6.0"
EXE_URL = f"https://github.com/GameTechDev/PresentMon/releases/download/v{VERSION}/PresentMon-{VERSION}-x64.exe"
EXE_SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
LICENSE_URL = f"https://raw.githubusercontent.com/GameTechDev/PresentMon/v{VERSION}/LICENSE.txt"
TIMEOUT_S = 60


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Pomo-build"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read()


def up_to_date() -> bool:
    try:
        return sha256(EXE.read_bytes()) == EXE_SHA256 and LICENSE.is_file()
    except OSError:
        return False


def main(argv: list[str]) -> int:
    if "--force" not in argv and up_to_date():
        print(f"PresentMon {VERSION} is already in {EXE.relative_to(ROOT)}")
        return 0
    print(f"Downloading PresentMon {VERSION}...")
    try:
        data = download(EXE_URL)
        licence = download(LICENSE_URL).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    got = sha256(data)
    if got != EXE_SHA256:
        print(f"Refusing PresentMon: its SHA-256 is {got}, expected {EXE_SHA256}", file=sys.stderr)
        return 1
    if "Permission is hereby granted" not in licence:
        print("Refusing PresentMon: its LICENSE.txt isn't the MIT licence it should be", file=sys.stderr)
        return 1
    BIN.mkdir(parents=True, exist_ok=True)
    tmp = EXE.with_suffix(".exe.part")
    tmp.write_bytes(data)
    tmp.replace(EXE)
    LICENSE.write_text(f"PresentMon {VERSION} (https://github.com/GameTechDev/PresentMon)\n\n{licence}",
                       encoding="utf-8")
    print(f"Saved {EXE.relative_to(ROOT)} and {LICENSE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
