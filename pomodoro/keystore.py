"""The user's Anthropic API key, encrypted at rest.

On Windows the key is sealed with DPAPI (CryptProtectData), so only the same
Windows account can read it back. Elsewhere it is stored in a file readable
only by the user.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ENTROPY = b"FocusOrb/ai-key/v1"  # unchanged since 1.1 (as FocusOrb), so saved keys still decrypt


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> Blob:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    src, extra, out = blob(data), blob(ENTROPY), Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "Pomo", ctypes.byref(extra), None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(extra), None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError("Windows could not encrypt or decrypt the API key.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def save_key(path: Path, key: str) -> None:
    key = key.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path.write_bytes(_dpapi(key.encode("utf-8"), protect=True))
    else:
        path.write_bytes(base64.b64encode(key.encode("utf-8")))
        os.chmod(path, 0o600)


def load_key(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = _dpapi(raw, protect=False) if sys.platform == "win32" else base64.b64decode(raw)
        return data.decode("utf-8").strip() or None
    except (OSError, ValueError):
        return None


def delete_key(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def mask(key: str) -> str:
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "•" * 8
