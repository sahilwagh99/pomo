"""Your plan: tasks lined up for the day, worked through one focus session at a time.

Ask Pomo fills it with tools ("45 minutes on the thesis, then 10 off, three rounds");
you can see and clear it from the right-click menu. When a focus session finishes,
the current task's count goes down and the next task takes over.
"""
from __future__ import annotations

from .engine import Mode, State
from .stats import plural

MAX_TOOL_ROUNDS = 4  # tool calls Pomo may chain for one message

TOOLS = [
    {
        "name": "start_session",
        "description": "Start a timer session right now: a focus session or a break. Use it when the user asks to "
                       "start, or agrees to start. minutes 0 means the user's usual length for that kind.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["focus", "short_break", "long_break"]},
                "minutes": {"type": "integer", "description": "Length in minutes (1-180), or 0 for the usual length"},
            },
            "required": ["kind", "minutes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_task",
        "description": "Set what the user is working on now. It shows on the timer and is logged with their sessions.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "Short task name, under 60 characters"}},
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_tasks",
        "description": "Line up tasks to work through, each with how many focus sessions it gets. The first becomes "
                       "the current task; when its sessions are done Pomo moves on to the next. replace=false adds "
                       "to the existing plan. An empty list with replace=true clears the plan.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "pomodoros": {"type": "integer", "description": "Focus sessions for this task (1-12)"},
                        },
                        "required": ["task", "pomodoros"],
                        "additionalProperties": False,
                    },
                },
                "replace": {"type": "boolean"},
            },
            "required": ["tasks", "replace"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_lengths",
        "description": "Change the user's default session lengths in minutes. 0 leaves that one unchanged. Only use "
                       "it when the user asks for different lengths, or agrees to a suggestion.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "focus_minutes": {"type": "integer"},
                "short_break_minutes": {"type": "integer"},
                "long_break_minutes": {"type": "integer"},
            },
            "required": ["focus_minutes", "short_break_minutes", "long_break_minutes"],
            "additionalProperties": False,
        },
    },
]

KIND_MODES = {"focus": Mode.FOCUS, "short_break": Mode.SHORT, "long_break": Mode.LONG}


class ToolError(Exception):
    """The input doesn't fit the tool: told back to Claude so it can try again."""


def _int(args: dict, key: str, lo: int, hi: int, allow_zero: bool = False) -> int:
    value = args.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolError(f"{key} must be a whole number")
    if allow_zero and value == 0:
        return 0
    if not lo <= value <= hi:
        raise ToolError(f"{key} must be between {lo} and {hi}" + (" (or 0)" if allow_zero else ""))
    return value


def _text(args: dict, key: str, limit: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} must be a non-empty string")
    return value.strip()[:limit]


# ------------------------------------------------------------------ the plan
def current_item(settings) -> dict | None:
    return settings.task_queue[0] if settings.task_queue else None


def set_plan(settings, items: list[dict], replace: bool) -> None:
    clean = [{"task": str(i["task"]).strip()[:120], "pomodoros": max(1, min(12, int(i["pomodoros"])))}
             for i in items if str(i.get("task", "")).strip()]
    settings.task_queue = (clean if replace else settings.task_queue + clean)[:20]


def advance(settings) -> tuple[str | None, bool]:
    """A focus session finished: count it against the current task. Returns (task now, plan just finished)."""
    if not settings.task_queue:
        return None, False
    head = settings.task_queue[0]
    head["pomodoros"] -= 1
    if head["pomodoros"] <= 0:
        settings.task_queue.pop(0)
        if not settings.task_queue:
            return None, True
    return settings.task_queue[0]["task"], False


def plan_text(settings) -> str:
    """'Thesis writing (2 left), then Reading (1)' for the menu, cards and Ask Pomo's context."""
    if not settings.task_queue:
        return ""
    head, rest = settings.task_queue[0], settings.task_queue[1:]
    text = f"{head['task']} ({head['pomodoros']} left)"
    if rest:
        text += ", then " + ", ".join(f"{t['task']} ({t['pomodoros']})" for t in rest[:5])
    return text


# ------------------------------------------------------------- tool calls
def run_tool(ctrl, name: str, args) -> tuple[str, str]:
    """Do what a tool call asks. Returns (result for Claude, a short note for the chat).

    Raises ToolError for input that doesn't fit, which is returned to Claude as an error.
    """
    if not isinstance(args, dict):
        raise ToolError("input must be an object")
    e, s = ctrl.engine, ctrl.settings
    if name == "start_session":
        kind = args.get("kind")
        if kind not in KIND_MODES:
            raise ToolError("kind must be focus, short_break or long_break")
        minutes = _int(args, "minutes", 1, 180, allow_zero=True)
        mode = KIND_MODES[kind]
        if e.mode is not mode or e.state in (State.RUNNING, State.PAUSED):
            e.switch_mode(mode)
        if minutes:
            e.set_duration(minutes * 60)
        e.start()
        length = minutes or round(e.total / 60)
        label = mode.label.lower()
        return f"Started a {length}-minute {label}.", f"Started a {length}-minute {label}"
    if name == "set_task":
        task = _text(args, "task", 120)
        ctrl.set_task(task)
        return f"Current task is now: {task}", f"Task: {task}"
    if name == "plan_tasks":
        tasks, replace = args.get("tasks"), args.get("replace")
        if not isinstance(tasks, list) or not isinstance(replace, bool):
            raise ToolError("tasks must be a list and replace a boolean")
        items = [{"task": _text(t, "task", 120), "pomodoros": _int(t, "pomodoros", 1, 12)}
                 for t in tasks if isinstance(t, dict)]
        if len(items) != len(tasks):
            raise ToolError("each task needs a task name and a number of pomodoros")
        set_plan(s, items, replace)
        ctrl.plan_changed()
        if not s.task_queue:
            return "The plan is cleared.", "Cleared the plan"
        total = sum(t["pomodoros"] for t in s.task_queue)
        return (f"Plan set: {plan_text(s)}. Current task: {s.task_queue[0]['task']}.",
                f"Planned {plural(len(s.task_queue), 'task')}, {plural(total, 'pomodoro')}")
    if name == "set_lengths":
        focus = _int(args, "focus_minutes", 1, 180, allow_zero=True)
        short = _int(args, "short_break_minutes", 1, 60, allow_zero=True)
        long_ = _int(args, "long_break_minutes", 1, 120, allow_zero=True)
        changed = []
        new = s.copy()
        for attr, value, label in (("focus_min", focus, "focus"), ("short_min", short, "short break"),
                                   ("long_min", long_, "long break")):
            if value:
                setattr(new, attr, value)
                changed.append(f"{label} {value} min")
        if not changed:
            return "Nothing changed.", ""
        ctrl.apply_settings(new)
        return "Lengths set: " + ", ".join(changed) + ".", "Set " + ", ".join(changed)
    raise ToolError(f"there is no tool called {name}")
