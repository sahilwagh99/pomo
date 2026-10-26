"""Connect Pomo to more coding agents: OpenAI's Codex CLI and Google's Gemini CLI.

Both take hooks much like Claude Code's (see claude_hooks.py), each in its own file:
  Codex CLI   ~/.codex/hooks.json       Claude Code's event names; timeouts in seconds; "async" runs in the
                                        background. Codex asks you to trust new hooks once (/hooks in Codex).
  Gemini CLI  ~/.gemini/settings.json   its own event names (BeforeAgent, AfterTool, AfterAgent...);
                                        timeouts in milliseconds; every hook runs in line, so Pomo only
                                        listens to the few events it needs.
The hook client (hook.py, run with --agent codex or --agent gemini) translates their events into
Claude Code's terms, so the rest of Pomo treats every agent the same way.

As with Claude Code, only Pomo's own entries (recognised by --pomo-hook) are ever added or removed,
a one-time backup is written before the first change, and a file that isn't valid JSON is left alone.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .claude_hooks import HookConfigError, _is_ours, _load, _signature, _strip_ours, _write, hook_command
from .hook import AGENT_FLAG, ANSWER_FLAG

GEMINI_WATCHED_TOOLS = "write_file|replace|run_shell_command"  # Gemini waits on every hook: only edits and commands


@dataclass(frozen=True)
class AgentInfo:
    key: str
    label: str        # "Codex CLI"
    short: str        # "Codex", for cards: "api: Codex finished"
    folder: str       # the agent's folder in your home directory
    file: str         # where its hooks live, inside that folder
    command: str      # the program, to tell whether it's installed
    trust_hint: str   # what to do in the agent after connecting


AGENTS: dict[str, AgentInfo] = {a.key: a for a in (
    AgentInfo("codex", "Codex CLI", "Codex", ".codex", "hooks.json", "codex",
              "In Codex, run /hooks once and trust Pomo's hooks."),
    AgentInfo("gemini", "Gemini CLI", "Gemini", ".gemini", "settings.json", "gemini",
              "New Gemini CLI sessions pick this up."),
)}


def settings_path(agent: str) -> Path:
    info = AGENTS[agent]
    return Path.home() / info.folder / info.file


def detected(agent: str) -> bool:
    """True if the agent seems to be installed: its folder exists or its command is on the PATH."""
    info = AGENTS[agent]
    return (Path.home() / info.folder).is_dir() or shutil.which(info.command) is not None


def _short_path_name(path: str) -> str:
    """Windows' 8.3 form of a path (no spaces), or "" when there isn't one."""
    if sys.platform != "win32":
        return ""
    import ctypes

    size = ctypes.windll.kernel32.GetShortPathNameW(path, None, 0)
    if not size:
        return ""
    buf = ctypes.create_unicode_buffer(size)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(path, buf, size) else ""


def _word(text: str) -> str:
    """A path that every shell (cmd, PowerShell, sh) reads as one word."""
    if " " not in text:
        return text
    short = _short_path_name(text)
    if short and " " not in short:
        return short
    return f'"{text}"'


def command_line(agent: str, answer: bool = False) -> str:
    """The shell command an agent runs for Pomo's hook. These agents take one string, not a list."""
    exe, args = hook_command()
    words = [_word(exe)] + [_word(a) for a in args] + [AGENT_FLAG, agent] + ([ANSWER_FLAG] if answer else [])
    return " ".join(words)


def layout(agent: str, command: str, answering: str, wait_timeout: int = 330) -> dict[str, list[dict]]:
    """Every hook group Pomo adds for an agent, by event."""
    out: dict[str, list[dict]] = {}

    def add(event: str, handler: dict, matcher: str | None = None) -> None:
        group = {"hooks": [handler]}
        if matcher:
            group = {"matcher": matcher, **group}
        out.setdefault(event, []).append(group)

    if agent == "codex":
        for event in ("SessionStart", "UserPromptSubmit"):
            add(event, {"type": "command", "command": command, "async": True, "timeout": 10})
        for event in ("PreToolUse", "PostToolUse"):
            add(event, {"type": "command", "command": command, "async": True, "timeout": 10}, "*")
        for event in ("SessionEnd", "Interrupt"):  # these always run in line, for at most 3 seconds
            add(event, {"type": "command", "command": command, "timeout": 3})
        add("PermissionRequest", {"type": "command", "command": command, "timeout": int(wait_timeout)}, "*")
        add("Stop", {"type": "command", "command": answering, "timeout": int(wait_timeout)})
    elif agent == "gemini":
        quick = {"name": "pomo", "type": "command", "command": command, "timeout": 5000}
        for event in ("SessionStart", "BeforeAgent", "Notification", "SessionEnd"):
            add(event, dict(quick), "*")
        add("AfterTool", dict(quick), GEMINI_WATCHED_TOOLS)
        add("AfterAgent", {"name": "pomo-reply", "type": "command", "command": answering,
                           "timeout": int(wait_timeout) * 1000}, "*")
    else:
        raise ValueError(agent)
    return out


def install(agent: str, path: Path | None = None, wait_timeout: int = 330) -> Path:
    path = path or settings_path(agent)
    data = _load(path)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = data["hooks"] = {}
    if not isinstance(hooks, dict):
        raise HookConfigError(f'"hooks" in {path} is not an object. It was left unchanged.')
    _strip_ours(hooks)
    for event, groups in layout(agent, command_line(agent), command_line(agent, answer=True), wait_timeout).items():
        hooks.setdefault(event, []).extend(groups)
    _write(path, data)
    return path


def uninstall(agent: str, path: Path | None = None) -> Path:
    path = path or settings_path(agent)
    data = _load(path)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        _strip_ours(hooks)
        if not hooks:
            del data["hooks"]
        _write(path, data)
    return path


def _ours(hooks: dict) -> set[tuple]:
    return {_signature(event, g, h) for event, groups in hooks.items() if isinstance(groups, list)
            for g in groups if isinstance(g, dict) and isinstance(g.get("hooks"), list)
            for h in g["hooks"] if _is_ours(h)}


def status(agent: str, path: Path | None = None) -> str:
    """'connected', 'outdated' (points at another Pomo location), or 'off'."""
    path = path or settings_path(agent)
    if not path.exists():
        return "off"
    try:
        data = _load(path)
    except HookConfigError:
        return "off"
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    installed = _ours(hooks)
    if not installed:
        return "off"
    expected = _ours(layout(agent, command_line(agent), command_line(agent, answer=True)))
    return "connected" if installed == expected else "outdated"


def refresh(agent: str, path: Path | None = None) -> bool:
    """Re-point existing hooks at this copy of Pomo (after a move or upgrade). True if anything changed."""
    path = path or settings_path(agent)
    if status(agent, path) != "outdated":
        return False
    install(agent, path)
    return True
