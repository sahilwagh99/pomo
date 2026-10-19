"""Claude Code hook client, run as `Pomo.exe --pomo-hook` (see claude_hooks.py).

Claude Code starts this for each hook event with the event JSON on stdin. It
forwards a compact summary to the running Pomo over a local named pipe.

For PermissionRequest it waits for the user's answer in Pomo and prints the
decision JSON Claude Code expects. In every other case (Pomo not running,
"answer in terminal", no answer in time, anything unexpected) it prints nothing,
so Claude Code shows its normal permission prompt. It always exits 0: a hook
must never break the agent.

Standard library only, no Qt: it has to start fast.
"""
from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import time

MARKER = "--pomo-hook"
LEGACY_MARKERS = ("--focusorb-hook",)  # written by 1.1, when the app was called FocusOrb
PROTOCOL = 1
CLIP = 300
CLIP_APPROVAL = 1200  # show the whole command when the user is asked to approve it


def pipe_name() -> str:
    """Server name for QLocalServer; also the Windows pipe name. Tests can override it."""
    return os.environ.get("POMO_AGENT_PIPE") or f"Pomo-agents-{getpass.getuser()}"


def _clip(text, limit: int = CLIP) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _short_path(path: str) -> str:
    path = str(path or "").replace("\\", "/").rstrip("/")
    parts = path.split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else path


def describe_tool(tool: str, tool_input, limit: int = CLIP) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if tool == "Bash":
        return _clip(tool_input.get("command", ""), limit)
    if tool in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return _short_path(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if tool in ("Glob", "Grep"):
        return _clip(tool_input.get("pattern", ""), limit)
    if tool == "WebFetch":
        return _clip(tool_input.get("url", ""), limit)
    if tool == "WebSearch":
        return _clip(tool_input.get("query", ""), limit)
    for key in ("description", "prompt", "query", "path", "url", "name"):
        if isinstance(tool_input.get(key), str):
            return _clip(tool_input[key], limit)
    return ""


def _lines(text) -> int:
    text = str(text or "")
    return text.count("\n") + 1 if text else 0


def edit_size(tool: str, tool_input) -> tuple[int, int]:
    """(lines added, lines removed) for file-editing tools."""
    if not isinstance(tool_input, dict):
        return 0, 0
    if tool == "Write":
        return _lines(tool_input.get("content")), 0
    if tool == "Edit":
        return _lines(tool_input.get("new_string")), _lines(tool_input.get("old_string"))
    if tool == "MultiEdit":
        edits = tool_input.get("edits") or []
        return (sum(_lines(e.get("new_string")) for e in edits if isinstance(e, dict)),
                sum(_lines(e.get("old_string")) for e in edits if isinstance(e, dict)))
    return 0, 0


def summarize(event: dict) -> dict:
    """The compact message Pomo receives: no file contents, clipped text."""
    name = str(event.get("hook_event_name", ""))
    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input")
    approval = name == "PermissionRequest"
    msg = {
        "v": PROTOCOL,
        "event": name,
        "session": str(event.get("session_id", "")),
        "cwd": str(event.get("cwd", "")),
        "tool": tool,
        "detail": describe_tool(tool, tool_input, CLIP_APPROVAL if approval else CLIP),
        "kind": "",
        "added": 0,
        "removed": 0,
        "wait": approval,
    }
    if name == "UserPromptSubmit":
        msg["detail"] = _clip(event.get("prompt", ""))
    elif name == "Notification":
        msg["detail"] = _clip(event.get("message", ""))
        msg["kind"] = str(event.get("notification_type", ""))
    elif name in ("Stop", "SubagentStop"):
        msg["detail"] = _clip(event.get("last_assistant_message", ""))
    elif name == "SessionStart":
        msg["kind"] = str(event.get("source", ""))
    elif name == "SessionEnd":
        msg["kind"] = str(event.get("reason", ""))
    elif name == "PostToolUse":
        msg["added"], msg["removed"] = edit_size(tool, tool_input)
    return msg


def decision_output(reply: dict) -> dict | None:
    """Claude Code's PermissionRequest output for Pomo's reply, or None to stay silent."""
    decision = reply.get("decision") if isinstance(reply, dict) else None
    if decision == "allow":
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow"}}}
    if decision == "deny":
        message = _clip(reply.get("message") or "Denied from Pomo.", 300)
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "deny", "message": message, "interrupt": False}}}
    return None


# ------------------------------------------------------------------ transport
def _connect(timeout: float = 1.5):
    """A binary file-like connection to Pomo, or None if it isn't running."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if os.name == "nt":
                return open(r"\\.\pipe" + "\\" + pipe_name(), "r+b", buffering=0)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(os.path.join(os.environ.get("TMPDIR", "/tmp"), pipe_name()))
            return sock.makefile("rwb", buffering=0)
        except FileNotFoundError:
            return None  # nothing listening: Pomo is not running
        except OSError:
            if time.monotonic() >= deadline:  # all pipe instances busy for too long
                return None
            time.sleep(0.05)


def _std_stream(index: int):
    """stdin (0) or stdout (1) as a binary stream, even in a windowed .exe without sys.stdin."""
    stream = sys.stdin if index == 0 else sys.stdout
    if stream is not None and getattr(stream, "buffer", None) is not None:
        return stream.buffer
    if os.name != "nt":
        return None
    import ctypes
    import msvcrt

    handle = ctypes.windll.kernel32.GetStdHandle(-10 if index == 0 else -11)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if index == 0 else os.O_WRONLY)
    return os.fdopen(fd, "rb" if index == 0 else "wb", buffering=0)


def run() -> int:
    try:
        stdin = _std_stream(0)
        event = json.loads((stdin.read() if stdin else b"") or b"{}")
        if not isinstance(event, dict):
            return 0
        msg = summarize(event)
        conn = _connect()
        if conn is None:
            return 0
        try:
            conn.write((json.dumps(msg) + "\n").encode("utf-8"))
            line = conn.readline() if msg["wait"] else b""
        finally:
            conn.close()
        if not line:
            return 0
        out = decision_output(json.loads(line))
        stdout = _std_stream(1)
        if out is not None and stdout is not None:
            stdout.write(json.dumps(out).encode("utf-8"))
            stdout.flush()
    except Exception:  # never break the agent because of Pomo
        pass
    return 0
