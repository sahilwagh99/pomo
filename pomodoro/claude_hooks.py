"""Register Pomo's hooks in Claude Code's user settings (~/.claude/settings.json).

Only entries that run Pomo's hook (recognised by the --pomo-hook argument)
are ever added or removed; everything else in the file is preserved. A one-time
backup is written next to the file before the first change, and a file that
does not parse as JSON is never overwritten.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .hook import ANSWER_FLAG, LEGACY_MARKERS, MARKER

# Watched without slowing Claude Code down: async hooks run in the background.
OBSERVED_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                   "Notification", "SubagentStop", "SessionEnd")
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PermissionRequest")
APPROVAL_EVENT = "PermissionRequest"
# These wait for the user (Pomo answers within its own time limit, or passes at once):
# permission prompts, Claude's multiple-choice questions, and the end of a turn (to reply).
QUESTION_TOOL = "AskUserQuestion"


class HookConfigError(Exception):
    pass


def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hook_command() -> tuple[str, list[str]]:
    """Executable and arguments that run Pomo's hook client."""
    if getattr(sys, "frozen", False):
        return sys.executable, [MARKER]
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")  # hooks need stdin/stdout, so not pythonw
    script = Path(__file__).resolve().parent.parent / "main.py"
    return str(console if console.exists() else exe), [str(script), MARKER]


def _is_ours(handler) -> bool:
    if not isinstance(handler, dict):
        return False
    args = handler.get("args")
    markers = (MARKER, *LEGACY_MARKERS)
    if isinstance(args, list) and any(m in args for m in markers):
        return True
    return any(m in str(handler.get("command", "")) for m in markers)


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        raise HookConfigError(f"Could not read {path}: {exc}. It was left unchanged.") from exc
    if not isinstance(data, dict):
        raise HookConfigError(f"{path} is not a JSON object. It was left unchanged.")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".pomo-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".pomo-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _strip_ours(hooks: dict) -> None:
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                handlers = [h for h in group["hooks"] if not _is_ours(h)]
                if not handlers:
                    continue
                group = {**group, "hooks": handlers}
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]


def layout(command: str, args: list[str], wait_timeout: int = 330) -> dict[str, list[dict]]:
    """Every hook group Pomo adds, by event."""
    out: dict[str, list[dict]] = {}

    def add(event: str, handler: dict, matcher: str | None = None) -> None:
        group = {"hooks": [handler]}
        if matcher:
            group = {"matcher": matcher, **group}
        out.setdefault(event, []).append(group)

    for event in OBSERVED_EVENTS:
        add(event, {"type": "command", "command": command, "args": list(args), "async": True, "timeout": 10},
            "*" if event in TOOL_EVENTS else None)
    add(APPROVAL_EVENT, {"type": "command", "command": command, "args": list(args), "timeout": int(wait_timeout)}, "*")
    answering = list(args) + [ANSWER_FLAG]
    add("PreToolUse", {"type": "command", "command": command, "args": answering, "timeout": int(wait_timeout)},
        QUESTION_TOOL)
    add("Stop", {"type": "command", "command": command, "args": answering, "timeout": int(wait_timeout)})
    return out


def install(path: Path | None = None, approval_timeout: int = 330) -> Path:
    path = path or default_settings_path()
    data = _load(path)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = data["hooks"] = {}
    if not isinstance(hooks, dict):
        raise HookConfigError(f'"hooks" in {path} is not an object. It was left unchanged.')
    _strip_ours(hooks)
    command, args = hook_command()
    for event, groups in layout(command, args, approval_timeout).items():
        hooks.setdefault(event, []).extend(groups)
    _write(path, data)
    return path


def _signature(event: str, group: dict, handler: dict) -> tuple:
    return (event, group.get("matcher"), handler.get("command"), tuple(handler.get("args") or ()),
            bool(handler.get("async")))


def uninstall(path: Path | None = None) -> Path:
    path = path or default_settings_path()
    data = _load(path)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        _strip_ours(hooks)
        if not hooks:
            del data["hooks"]
        _write(path, data)
    return path


def status(path: Path | None = None) -> str:
    """'connected', 'outdated' (points at another Pomo location), or 'off'."""
    path = path or default_settings_path()
    try:
        data = _load(path)
    except HookConfigError:
        return "off"
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    ours = [h for groups in hooks.values() if isinstance(groups, list)
            for g in groups if isinstance(g, dict) and isinstance(g.get("hooks"), list)
            for h in g["hooks"] if _is_ours(h)]
    if not ours:
        return "off"
    command, args = hook_command()
    installed = {_signature(event, g, h) for event, groups in hooks.items() if isinstance(groups, list)
                 for g in groups if isinstance(g, dict) and isinstance(g.get("hooks"), list)
                 for h in g["hooks"] if _is_ours(h)}
    expected = {_signature(event, g, h) for event, groups in layout(command, args).items()
                for g in groups for h in g["hooks"]}
    return "connected" if installed == expected else "outdated"


def refresh(path: Path | None = None) -> bool:
    """Re-point existing hooks at this copy of Pomo (after a move, rename or upgrade).

    Only acts when the user connected Claude Code before; returns True if it changed anything.
    """
    path = path or default_settings_path()
    if status(path) != "outdated":
        return False
    install(path, approval_timeout=330)
    return True
