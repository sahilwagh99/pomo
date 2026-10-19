"""Ask Pomo: chat with Claude about your focus, your plans and your coding agents.

Calls Claude through the official Anthropic SDK. Replies stream; the model is
Claude Opus 5.5 unless the user picks another in Settings; server-side refusal
fallbacks are on. Nothing is sent until the user asks something, and then only
the conversation plus a short Pomo context summary (timer, focus stats,
task names and agent activity).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QThread, Signal

from .engine import State
from .stats import fmt_clock, fmt_day, fmt_hm, fmt_hour, streaks, summarize, summarize_agents

FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_FALLBACK_MODELS = ("claude-haiku-5-5",)  # Haiku has no server-side fallback

SYSTEM_PROMPT = """You are Pomo, the focus buddy inside Pomo, a Pomodoro timer for Windows. You look like a small matcha mochi with a sakura blossom, and you sit on the user's timer.

Help the user plan focused work, reflect on their focus habits and keep their momentum. Keep replies short and warm (usually under 120 words), practical, and never guilt-tripping. Markdown is rendered, so short paragraphs and simple lists work well.

When planning, break the work into steps that each fit one focus session at the user's focus length, as a numbered list, and say which one to start now.

After each user message you get a Pomo context message with live facts: the time, the timer, today's and this week's focus stats, the current task, and what their coding agents (Claude Code sessions) did today. Use it for facts and never invent numbers. If something you need isn't there, say so or ask."""

QUICK_ACTIONS = (
    ("plan", "Plan my task"),
    ("review", "How am I doing?"),
    ("agents", "Agents today"),
    ("pep", "Pep talk"),
)


def quick_prompt(action: str, task: str) -> str:
    if action == "plan":
        return (f"Help me plan \"{task}\" into pomodoros for today." if task
                else "Help me plan my next few pomodoros. Ask me what I'm working on if you need to.")
    return {
        "review": "How has my focus been this week? Give me two or three specific observations and one suggestion.",
        "agents": "Summarize what my coding agents did today, by project, and anything that still needs me.",
        "pep": "Give me a short pep talk for my next focus session.",
    }[action]


def build_context(ctrl, now: datetime | None = None) -> str:
    """Live facts for this turn, sent as a mid-conversation system message."""
    now = now or datetime.now()
    s, e = ctrl.settings, ctrl.engine
    day = now.date()
    records = ctrl.storage.sessions()
    today = summarize(records, day, day)
    week = summarize(records, day - timedelta(days=6), day)
    current, best = streaks(records, day)
    state = {State.RUNNING: "running", State.PAUSED: "paused", State.IDLE: "not started",
             State.FINISHED: "the previous session just ended and this one is ready"}[e.state]
    lines = [
        f"Now: {now:%A} {now:%Y-%m-%d %H:%M}.",
        f"Timer: {e.mode.label}, {state}, {fmt_clock(e.remaining)} left.",
        f"Settings: focus {s.focus_min} min, short break {s.short_min} min, long break {s.long_min} min "
        f"after every {s.sessions_before_long} pomodoros; daily goal {s.daily_goal} pomodoros.",
        f"Current task: {e.task}." if e.task else "Current task: none set.",
        f"Today: {today.pomodoros} of {s.daily_goal} pomodoros done, {fmt_hm(today.focus_sec)} focused, "
        f"{today.interrupted} focus sessions stopped early.",
    ]
    rate = f"{week.completion_rate:.0%}" if week.completion_rate is not None else "n/a"
    week_line = (f"Last 7 days: {week.pomodoros} pomodoros, {fmt_hm(week.focus_sec)} focused on "
                 f"{week.active_days} of 7 days, completion rate {rate}")
    if week.peak_hour is not None:
        week_line += f", most focus around {fmt_hour(week.peak_hour)}"
    if week.best_day is not None:
        week_line += f", best day {fmt_day(week.best_day.day)} ({fmt_hm(week.best_day.focus_sec)})"
    lines.append(week_line + ".")
    if week.tasks:
        lines.append("Top tasks this week: " + ", ".join(f"{name} {fmt_hm(sec)}" for name, sec, _ in week.tasks[:4]) + ".")
    lines.append(f"Streak: {current} day(s) in a row; best {best}.")

    agents = summarize_agents(ctrl.storage.agent_events(since=day), day)
    if agents.sessions:
        lines.append(
            f"Coding agents today: {agents.sessions} session(s), {agents.prompts} prompt(s), {agents.tool_calls} tool calls, "
            f"{agents.files_edited} file(s) edited (+{agents.lines_added}/-{agents.lines_removed} lines), "
            f"{agents.commands} command(s), {agents.turns} finished turn(s)."
        )
        if agents.projects:
            lines.append("Agent activity by project: " + ", ".join(f"{p} ({n} tool calls)" for p, n in agents.projects[:5]) + ".")
        if agents.recent_prompts:
            lines.append("Recent prompts to agents: " + " | ".join(agents.recent_prompts[-5:]))
    hub = getattr(ctrl, "agents", None)
    if hub is not None and hub.sessions:
        live = "; ".join(f"{a.project}: {a.status_text}" + (f" ({a.activity})" if a.status == "working" else "")
                         for a in hub.live_sessions()[:4])
        lines.append(f"Agent sessions right now: {live}.")
    elif not agents.sessions:
        lines.append("Coding agents: no activity recorded today.")
    return "Pomo context for this message:\n- " + "\n- ".join(lines)


class ChatWorker(QThread):
    """Streams one reply on a background thread."""

    text = Signal(str)
    completed = Signal(object)  # the final message
    refused = Signal()
    failed = Signal(str)

    def __init__(self, api_key: str | None, model: str, messages: list, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.model = model
        self.messages = list(messages)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            import anthropic
        except ImportError:
            self.failed.emit("The Anthropic SDK is not installed (pip install anthropic).")
            return
        try:
            client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
            kwargs = dict(
                model=self.model,
                max_tokens=64000,
                system=SYSTEM_PROMPT,
                messages=self.messages,
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
            )
            if self.model not in NO_FALLBACK_MODELS:
                kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
            with client.beta.messages.stream(**kwargs) as stream:
                for chunk in stream.text_stream:
                    if self._cancel:
                        return  # leaving the block closes the connection
                    self.text.emit(chunk)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                self.refused.emit()
            else:
                self.completed.emit(final)
        except anthropic.AuthenticationError:
            self.failed.emit("Anthropic didn't accept your API key. Tap **Key** above to check or replace it.")
        except anthropic.PermissionDeniedError:
            self.failed.emit("Your API key isn't allowed to use this model. Pick another in Settings > AI.")
        except anthropic.NotFoundError:
            self.failed.emit(f"{self.model} isn't available to your API key. Pick another model in Settings > AI.")
        except anthropic.RateLimitError:
            self.failed.emit("Anthropic is rate-limiting requests right now. Try again in a minute.")
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                self.failed.emit(f"Anthropic is having trouble right now (error {exc.status_code}). Try again shortly.")
            else:
                self.failed.emit(f"The request failed: {exc.message}")
        except anthropic.APIConnectionError:
            self.failed.emit("Can't reach Anthropic. Check your internet connection.")
        except anthropic.AnthropicError:
            self.failed.emit("No API key yet. Tap **Key** above to add yours.")
        except Exception as exc:  # keep the chat usable whatever happens
            self.failed.emit(f"Something went wrong: {exc}")


KEYS_URL = "https://console.anthropic.com/settings/keys"


class KeyCheckWorker(QThread):
    """Checks a key with a free Models API lookup (no tokens are used).

    result(status, message): status is "ok", "warn" (key works with a caveat), "bad" (key rejected)
    or "error" (couldn't tell, e.g. offline).
    """

    result = Signal(str, str)

    def __init__(self, api_key: str | None, model: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.model = model

    def run(self) -> None:
        try:
            import anthropic
        except ImportError:
            self.result.emit("error", "The Anthropic SDK is not installed (pip install anthropic).")
            return
        try:
            client = anthropic.Anthropic(api_key=self.api_key, max_retries=1, timeout=20.0) if self.api_key \
                else anthropic.Anthropic(max_retries=1, timeout=20.0)
            client.models.retrieve(self.model)
            self.result.emit("ok", "Your key works. Pomo is ready to chat.")
        except anthropic.AuthenticationError:
            self.result.emit("bad", "Anthropic didn't accept that key. Check that you copied all of it.")
        except anthropic.PermissionDeniedError:
            self.result.emit("bad", "That key isn't allowed to use the API. Check it in the Anthropic Console.")
        except anthropic.NotFoundError:
            self.result.emit("warn", f"Your key works, but it can't use {self.model}. Pick another model in Settings > AI.")
        except anthropic.RateLimitError:
            self.result.emit("warn", "Your key works. Anthropic is busy right now, so replies may be slow.")
        except anthropic.APIStatusError as exc:
            self.result.emit("error", f"Anthropic answered with an error ({exc.status_code}). Try again shortly.")
        except anthropic.APIConnectionError:
            self.result.emit("error", "Can't reach Anthropic. Check your internet connection.")
        except anthropic.AnthropicError:
            self.result.emit("bad", "No API key found. Paste your key to get started.")
