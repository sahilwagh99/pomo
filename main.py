"""Pomo entry point.

    python main.py                   normal run
    python main.py --demo            separate data folder pre-filled with sample history
    python main.py --pomo-hook   Claude Code hook client (reads an event on stdin; no window)
    python main.py --media-check     print what Pomo sees playing, as JSON (no window)
"""
from __future__ import annotations

import sys

HOOK_FLAGS = ("--pomo-hook", "--focusorb-hook")  # the second one is from 1.1 (FocusOrb)


def main() -> int:
    # Qt is imported here, not at the top, so hook runs stay fast
    import argparse
    import getpass
    import hashlib
    from pathlib import Path

    from PySide6.QtGui import QFont
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from pomodoro import APP_NAME, winutil
    from pomodoro.app import PomoApp
    from pomodoro.config import default_data_dir, migrate_legacy_data
    from pomodoro.theme import FONT_FAMILY

    parser = argparse.ArgumentParser(prog=APP_NAME, add_help=False)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args, _ = parser.parse_known_args()
    if args.data_dir is None:
        migrate_legacy_data()
    data_dir = args.data_dir or default_data_dir()
    if args.demo:
        data_dir = data_dir / "demo"

    winutil.set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    font = QFont(FONT_FAMILY)
    font.setPixelSize(13)
    app.setFont(font)

    # Single instance per data folder: a second launch just brings the timer back.
    digest = hashlib.sha1(str(data_dir.resolve()).encode()).hexdigest()[:10]
    server_name = f"{APP_NAME}-{getpass.getuser()}-{digest}"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)
    winutil.hold_running_mutex()  # open until Pomo exits: the installer waits on it when Pomo updates itself

    ctrl = PomoApp(app, data_dir, demo=args.demo)

    def on_ping() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        ctrl.show_timer()
        ctrl.view.flash(1)

    server.newConnection.connect(on_ping)
    return app.exec()


def media_check() -> int:
    """Print what Pomo sees playing (Spotify, a browser tab, ...) as JSON, for troubleshooting."""
    import json

    from pomodoro.hook import _std_stream
    from pomodoro.media import check

    out = _std_stream(1)
    if out is not None:
        out.write((json.dumps(check(), indent=2) + "\n").encode("utf-8"))
        out.flush()
    return 0


if __name__ == "__main__":
    if any(flag in sys.argv for flag in HOOK_FLAGS):
        from pomodoro.hook import run

        sys.exit(run())
    if "--media-check" in sys.argv:
        sys.exit(media_check())
    sys.exit(main())
