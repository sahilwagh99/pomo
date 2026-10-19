"""FocusOrb entry point.

    python main.py            normal run
    python main.py --demo     separate data folder pre-filled with sample history
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from pomodoro import APP_NAME, winutil
from pomodoro.app import FocusOrbApp
from pomodoro.config import default_data_dir
from pomodoro.theme import FONT_FAMILY


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, add_help=False)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args, _ = parser.parse_known_args()
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

    ctrl = FocusOrbApp(app, data_dir, demo=args.demo)

    def on_ping() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        ctrl.show_orb()
        ctrl.orb.flash(1)

    server.newConnection.connect(on_ping)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
