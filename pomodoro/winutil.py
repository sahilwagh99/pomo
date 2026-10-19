"""Windows integration: start-up registration, title bar color, app identity."""
from __future__ import annotations

import sys
from pathlib import Path

from . import APP_NAME, LEGACY_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{pythonw if pythonw.exists() else exe}" "{script}"'


def set_start_with_windows(enabled: bool) -> bool:
    """Register (or remove) Pomo in the current user's Run key. Also clears FocusOrb's old entry."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            names = (LEGACY_NAME,) if enabled else (APP_NAME, LEGACY_NAME)
            for name in names:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
        return True
    except OSError:
        return False


def starts_with_windows() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return bool(value)
    except OSError:
        return False


def set_dark_title_bar(widget, dark: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, then pre-20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def set_app_user_model_id() -> None:
    """Lets Windows group the taskbar and toasts under Pomo instead of python.exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.FocusTimer")
    except Exception:
        pass
