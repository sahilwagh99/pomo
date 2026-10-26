"""Claude Code hook client, run as `Pomo.exe --pomo-hook` (see claude_hooks.py).

Claude Code starts this for each hook event with the event JSON on stdin. It
forwards a compact summary to the running Pomo over a local named pipe.

For PermissionRequest it waits for the user's answer in Pomo and prints the
decision JSON Claude Code expects. Run with --answer (see claude_hooks.py), it
also waits on two more events: Claude's multiple-choice questions (PreToolUse for
AskUserQuestion), answered with updatedInput, and the end of a turn (Stop), where a
reply from Pomo becomes Claude's next instruction. In every other case (Pomo not
running, "answer in terminal", no answer in time, anything unexpected) it prints
nothing, so Claude Code carries on as usual. It always exits 0: a hook must never
break the agent.

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
ANSWER_FLAG = "--answer"  # this run may wait for the user's answer (questions, replies)
LEGACY_MARKERS = ("--focusorb-hook",)  # written by 1.1, when the app was called FocusOrb
PROTOCOL = 1
CLIP = 300
CLIP_APPROVAL = 1200  # show the whole command when the user is asked to approve it
CLIP_MESSAGE = 8000   # Claude's own words, shown in full in Pomo


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


def _text(text, limit: int) -> str:
    """Clipped but with its line breaks: for messages shown as Claude wrote them."""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def questions_of(tool_input) -> list[dict]:
    """AskUserQuestion's questions, trimmed for display."""
    out = []
    for q in (tool_input.get("questions") if isinstance(tool_input, dict) else None) or []:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            continue
        options = [{"label": _clip(o.get("label"), 120), "description": _clip(o.get("description"), 300)}
                   for o in q.get("options") or [] if isinstance(o, dict) and o.get("label")]
        out.append({"question": _text(q["question"], 1000), "header": _clip(q.get("header"), 40),
                    "options": options[:8], "multiSelect": bool(q.get("multiSelect"))})
    return out[:4]


def summarize(event: dict, answer: bool = False) -> dict:
    """The compact message Pomo receives: no file contents, clipped text."""
    name = str(event.get("hook_event_name", ""))
    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input")
    approval = name == "PermissionRequest"
    question = answer and name == "PreToolUse" and tool == "AskUserQuestion"
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
        "wait": approval or question or (answer and name == "Stop"),
    }
    if name == "UserPromptSubmit":
        msg["detail"] = _clip(event.get("prompt", ""))
    elif name == "Notification":
        msg["detail"] = _clip(event.get("message", ""))
        msg["message"] = _text(event.get("message", ""), 2000)  # exactly as Claude Code shows it
        msg["title"] = _clip(event.get("title", ""), 120)
        msg["kind"] = str(event.get("notification_type", ""))
    elif name in ("Stop", "SubagentStop"):
        msg["detail"] = _clip(event.get("last_assistant_message", ""))
        msg["message"] = _text(event.get("last_assistant_message", ""), CLIP_MESSAGE)
        msg["kind"] = "continuing" if event.get("stop_hook_active") else ""
    elif name == "SessionStart":
        msg["kind"] = str(event.get("source", ""))
    elif name == "SessionEnd":
        msg["kind"] = str(event.get("reason", ""))
    elif name == "PostToolUse":
        msg["added"], msg["removed"] = edit_size(tool, tool_input)
    if question:
        msg["kind"] = "question"
        msg["questions"] = questions_of(tool_input)
    return msg


def decision_output(reply: dict, event: dict | None = None) -> dict | None:
    """Claude Code's hook output for Pomo's reply, or None to stay silent (Claude Code carries on as usual)."""
    decision = reply.get("decision") if isinstance(reply, dict) else None
    name = str((event or {}).get("hook_event_name", "PermissionRequest"))
    if name == "PreToolUse" and decision == "answer":
        tool_input = (event or {}).get("tool_input")
        answers = reply.get("answers")
        if not isinstance(tool_input, dict) or not isinstance(answers, list):
            return None
        # updatedInput replaces the whole input: echo the questions, add the answers keyed by question text
        keyed = {q["question"]: str(a) for q, a in zip(tool_input.get("questions") or [], answers)
                 if isinstance(q, dict) and isinstance(q.get("question"), str) and a not in (None, "")}
        if not keyed:
            return None
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": {**tool_input, "answers": keyed}}}
    if name == "Stop":
        text = str(reply.get("message") or "").strip() if decision == "reply" else ""
        return {"decision": "block", "reason": f"The user replied (from Pomo): {text}"} if text else None
    if name != "PermissionRequest":
        return None
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
        msg = summarize(event, answer=ANSWER_FLAG in sys.argv)
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
        out = decision_output(json.loads(line), event)
        stdout = _std_stream(1)
        if out is not None and stdout is not None:
            stdout.write(json.dumps(out).encode("utf-8"))
            stdout.flush()
    except Exception:  # never break the agent because of Pomo
        pass
    return 0
