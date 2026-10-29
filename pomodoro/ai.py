"""Ask Pomo: chat about your focus, your plans and your coding agents, with your own key.

Claude goes through the official Anthropic SDK (Claude Opus 5.5 unless the user picks another
model; server-side refusal fallbacks are on). OpenAI, Gemini, Grok and OpenRouter go through
their OpenAI-compatible chat endpoints. Replies stream. Nothing is sent until the user asks
something, and then only the conversation plus a short Pomo context summary (timer, focus
stats, task names and agent activity).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QThread, Signal

from .planner import TOOLS
from .providers import PROVIDERS, chat_models, provider

from .engine import State
from .stats import fmt_clock, fmt_day, fmt_hm, fmt_hour, streaks, summarize, summarize_agents

FALLBACK_BETA = "server-side-fallback-2026-07-01"
CURRENT_CLAUDE = {m for m, _label in PROVIDERS["anthropic"].models}  # models Pomo knows take effort settings
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}  # not Haiku

SYSTEM_PROMPT = """You are Pomo, the focus buddy inside Pomo, a Pomodoro timer for Windows. You look like a small matcha mochi with a sakura blossom, and you sit on the user's timer.

Help the user plan focused work, reflect on their focus habits and keep their momentum. Keep replies short and warm (usually under 120 words), practical, and never guilt-tripping. Markdown is rendered, so short paragraphs and simple lists work well.

When planning, break the work into steps that each fit one focus session at the user's focus length, as a numbered list, and say which one to start now.

Many users study on their own and feel unsure what to learn next, and some are learning English. When the user isn't sure which direction to take, help them choose one direction to try for a fixed number of weeks (an experiment they can change later, not a life decision). Ask two or three short questions first (what they study, what they want it to lead to, how much time they have each day), then make their path with set_path: 3 to 6 milestones, each with concrete steps that take 1 to 3 focus sessions, starting easy so the first step feels doable today. When they say they finished a step, mark it with complete_step. Write simply and clearly, in short sentences.

You can also act on the timer with your tools: start a focus session or a break, set the current task, line up a plan of tasks (each with a number of pomodoros), and change the default session lengths. Use them when the user asks you to set something up ("45 minutes on the thesis, then 10 off, three rounds") or agrees to your suggestion, then say in a sentence what you did. Don't start or change anything they didn't ask for.

After each user message you get a Pomo context message with live facts: the time, the timer, today's and this week's focus stats, the current task and plan, their study path and today's step, their study log, thoughts they parked during focus, how their sessions felt, and what their coding agents (Claude Code sessions) did today. Use it for facts and never invent numbers. If something you need isn't there, say so or ask."""

QUICK_ACTIONS = (
    ("path", "My path"),
    ("plan", "Plan my task"),
    ("review", "My week"),
    ("day", "My day"),
    ("agents", "Agents today"),
    ("pep", "Pep talk"),
)


def quick_prompt(action: str, task: str, has_path: bool = False) -> str:
    if action == "path":
        return (PATH_CHECK_PROMPT if has_path else PATH_START_PROMPT)
    if action == "plan":
        return (f"Help me plan \"{task}\" into pomodoros for today." if task
                else "Help me plan my next few pomodoros. Ask me what I'm working on if you need to.")
    return {
        "review": "How has my focus been this week? Give me two or three specific observations and one suggestion.",
        "day": ("Review my day: what I got done, how my sessions felt, what my agents did, and one thing to "
                "carry into tomorrow. Keep it short and kind."),
        "agents": "Summarize what my coding agents did today, by project, and anything that still needs me.",
        "pep": "Give me a short pep talk for my next focus session.",
    }[action]


PATH_START_PROMPT = ("I'm not sure what to study or which direction to take. Help me choose one direction to try "
                     "for 8 weeks. Ask me a few short questions first, then make my path.")
PATH_CHECK_PROMPT = "Look at my study path. Am I on track? Help me get started on today's step."
PATH_RETHINK_PROMPT = ("My path's weeks are up. Help me look back at how it went and decide: keep going, adjust "
                       "it, or try a different direction. Ask me how it felt first.")


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
    from .insights import focus_score, rating_counts
    from .planner import plan_text

    plan = plan_text(s)
    lines.append(f"Plan: {plan}." if plan else "Plan: none lined up.")
    from . import studypath, studylog

    lines.append(studypath.summary(s.study_path, day))
    lines.append(studylog.summary(ctrl.storage, day))
    ratings = rating_counts(records, day - timedelta(days=6), day)
    if any(ratings.values()):
        lines.append(f"How sessions felt (last 7 days): {ratings[3]} great, {ratings[2]} okay, {ratings[1]} tough.")
    score, distracted, total = focus_score(ctrl.storage.app_time(since=day))
    if score is not None:
        lines.append(f"Focus score today: {score:.0%} of focus time away from distracting apps "
                     f"({fmt_hm(distracted)} on them).")
    thoughts = ctrl.storage.thoughts()
    if thoughts:
        lines.append("Thoughts parked during focus, not yet handled: " + " | ".join(t.text for t in thoughts[:10]))

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


@dataclass
class ToolCall:
    id: str
    name: str
    input: object  # the parsed arguments, or None if they weren't valid JSON
    raw: str = ""


@dataclass
class Reply:
    """One finished reply, whichever provider wrote it."""
    text: str
    tool_calls: list[ToolCall]
    assistant_message: dict  # to append to the history, in the provider's own format
    stop_reason: str         # "end_turn" / "tool_use" / "max_tokens" ... (OpenAI-style reasons are mapped)
    complete: bool = True    # False when it was cut off, so tool inputs may be incomplete


class ChatWorker(QThread):
    """Streams one reply from Claude on a background thread (official Anthropic SDK)."""

    text = Signal(str)
    completed = Signal(object)  # Reply
    refused = Signal()
    failed = Signal(str)

    def __init__(self, api_key: str | None, model: str, messages: list, parent=None, tools: list | None = None,
                 system: str = ""):
        super().__init__(parent)
        self.api_key = api_key
        self.model = model
        self.messages = list(messages)
        self.tools = tools
        self.system = system or SYSTEM_PROMPT
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
            kwargs = dict(model=self.model, max_tokens=64000, system=self.system, messages=self.messages,
                          cache_control={"type": "ephemeral"})
            if self.model in CURRENT_CLAUDE:
                kwargs["output_config"] = {"effort": "medium"}
            if self.model in FALLBACK_MODELS:
                kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
            if self.tools:
                kwargs["tools"] = self.tools
            with client.beta.messages.stream(**kwargs) as stream:
                for chunk in stream.text_stream:
                    if self._cancel:
                        return  # leaving the block closes the connection
                    self.text.emit(chunk)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                self.refused.emit()
                return
            calls = [ToolCall(b.id, b.name, b.input) for b in final.content if getattr(b, "type", "") == "tool_use"]
            text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
            self.completed.emit(Reply(text, calls, {"role": "assistant", "content": final.content},
                                      final.stop_reason or "end_turn", final.stop_reason != "max_tokens"))
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


def compat_tools(provider_key: str) -> list[dict]:
    """Pomo's tools in the OpenAI function format. OpenAI enforces the schemas strictly; the other
    providers get the plain schema (some reject OpenAI's extra fields), and Pomo checks inputs anyway."""
    strict = provider_key == "openai"

    def loose(schema):
        if isinstance(schema, dict):
            return {k: loose(v) for k, v in schema.items() if k != "additionalProperties"}
        if isinstance(schema, list):
            return [loose(v) for v in schema]
        return schema

    out = []
    for tool in TOOLS:
        function = {"name": tool["name"], "description": tool["description"],
                    "parameters": tool["input_schema"] if strict else loose(tool["input_schema"])}
        if strict:
            function["strict"] = True
        out.append({"type": "function", "function": function})
    return out


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body[:200]
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error", data) if isinstance(data, dict) else {}
    return str(err.get("message") if isinstance(err, dict) else err)[:300]


def _friendly(label: str, model: str, exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.status in (401, 403):
            return f"{label} didn't accept your API key. Tap **Key** above to check or replace it."
        if exc.status == 404:
            return f"{model} isn't available with your {label} key. Pick another model in Settings > AI."
        if exc.status == 429:
            return f"{label} is rate-limiting requests (or your credit ran out). Try again in a minute."
        if exc.status >= 500:
            return f"{label} is having trouble right now (error {exc.status}). Try again shortly."
        return f"The request failed: {exc.message}"
    return f"Can't reach {label}. Check your internet connection."


class CompatChatWorker(QThread):
    """Streams one reply from an OpenAI-compatible endpoint: OpenAI, Gemini, Grok or OpenRouter."""

    text = Signal(str)
    completed = Signal(object)  # Reply
    refused = Signal()
    failed = Signal(str)

    def __init__(self, provider_key: str, api_key: str | None, model: str, messages: list, parent=None,
                 tools: list | None = None, transport=None, system: str = ""):
        super().__init__(parent)
        self.provider = provider(provider_key)
        self.api_key = api_key
        self.model = model
        self.messages = list(messages)
        self.tools = tools
        self.system = system or SYSTEM_PROMPT
        self.transport = transport  # tests pass a mock transport
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        import httpx2

        p = self.provider
        if not self.api_key:
            self.failed.emit(f"No {p.short} API key yet. Tap **Key** above to add yours.")
            return
        body = {"model": self.model, "stream": True,
                "messages": [{"role": "system", "content": self.system}] + self.messages}
        if self.tools:
            body["tools"] = self.tools
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if p.key == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/sahilwagh99/pomo", "X-Title": "Pomo"})
        text, calls, finish = "", {}, ""
        try:
            with httpx2.Client(timeout=httpx2.Timeout(120.0, connect=15.0), transport=self.transport) as client:
                with client.stream("POST", f"{p.base_url}/chat/completions", json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise ApiError(resp.status_code, _error_message(resp.text))
                    for line in resp.iter_lines():
                        if self._cancel:
                            return
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if chunk.get("error"):
                            raise ApiError(500, _error_message(data))
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            piece = delta.get("content") or delta.get("refusal") or ""
                            if piece:
                                text += piece
                                self.text.emit(piece)
                            for tc in delta.get("tool_calls") or []:
                                index = tc.get("index")
                                if index is None:  # some providers send each call whole, without an index
                                    index = len(calls)
                                slot = calls.setdefault(index, {"id": "", "name": "", "args": ""})
                                slot["id"] = tc.get("id") or slot["id"]
                                function = tc.get("function") or {}
                                slot["name"] = function.get("name") or slot["name"]
                                slot["args"] += function.get("arguments") or ""
                            finish = choice.get("finish_reason") or finish
        except ApiError as exc:
            self.failed.emit(_friendly(p.short, self.model, exc))
            return
        except (httpx2.TransportError, OSError) as exc:
            self.failed.emit(_friendly(p.short, self.model, exc))
            return
        except Exception as exc:  # keep the chat usable whatever happens
            self.failed.emit(f"Something went wrong: {exc}")
            return
        if finish == "content_filter" and not text and not calls:
            self.refused.emit()
            return
        tool_calls, wire_calls = [], []
        for i, slot in sorted(calls.items()):
            call_id = slot["id"] or f"call_{i}"
            try:
                args = json.loads(slot["args"] or "{}")
            except ValueError:
                args = None
            tool_calls.append(ToolCall(call_id, slot["name"], args, slot["args"]))
            wire_calls.append({"id": call_id, "type": "function",
                               "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}})
        message = {"role": "assistant", "content": text}
        if wire_calls:
            message["tool_calls"] = wire_calls
        stop = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}.get(finish, finish or "end_turn")
        if wire_calls and stop == "end_turn":
            stop = "tool_use"
        self.completed.emit(Reply(text, tool_calls, message, stop, finish != "length"))


def tool_results_messages(provider_key: str, results: list[tuple[str, str, bool]]) -> list[dict]:
    """Answers to every tool call of one reply: (call id, result text, is error)."""
    if provider(provider_key).kind == "anthropic":
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": text, **({"is_error": True} if err else {})}
            for cid, text, err in results]}]
    return [{"role": "tool", "tool_call_id": cid, "content": f"Error: {text}" if err else text}
            for cid, text, err in results]


def make_worker(provider_key: str, api_key: str | None, model: str, messages: list, parent=None,
                tools: bool = True, system: str = ""):
    """A worker for one reply. tools=False and a system prompt of its own make it a one-off helper
    (the English tutor) rather than Ask Pomo."""
    if provider(provider_key).kind == "anthropic":
        return ChatWorker(api_key, model, messages, parent, tools=TOOLS if tools else None, system=system)
    return CompatChatWorker(provider_key, api_key, model, messages, parent,
                            tools=compat_tools(provider_key) if tools else None, system=system)


KEYS_URL = PROVIDERS["anthropic"].keys_url


class KeyCheckWorker(QThread):
    """Checks a key with a free model-list lookup (no tokens are used), and brings back the models.

    result(status, message): status is "ok", "warn" (key works with a caveat), "bad" (key rejected)
    or "error" (couldn't tell, e.g. offline). models(list) fires with the provider's chat models.
    """

    result = Signal(str, str)
    models = Signal(list)

    def __init__(self, provider_key: str, api_key: str | None, model: str, parent=None, transport=None):
        super().__init__(parent)
        self.provider = provider(provider_key)
        self.api_key = api_key
        self.model = model
        self.transport = transport

    def run(self) -> None:
        if self.provider.kind == "anthropic":
            self._check_anthropic()
        else:
            self._check_compat()

    def _check_anthropic(self) -> None:
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
            try:
                self.models.emit(sorted(m.id for m in client.models.list(limit=100)))
            except Exception:
                pass
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

    def _check_compat(self) -> None:
        import httpx2

        p = self.provider
        if not self.api_key:
            self.result.emit("bad", f"No {p.short} key found. Paste your key to get started.")
            return
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx2.Client(timeout=20.0, transport=self.transport) as client:
                if p.key == "openrouter":  # its model list is public, so ask about the key itself
                    check = client.get(f"{p.base_url}/key", headers=headers)
                    if check.status_code in (401, 403):
                        self.result.emit("bad", "OpenRouter didn't accept that key. Check that you copied all of it.")
                        return
                resp = client.get(f"{p.base_url}/models", headers=headers)
        except (httpx2.TransportError, OSError):
            self.result.emit("error", f"Can't reach {p.short}. Check your internet connection.")
            return
        if resp.status_code in (401, 403):
            self.result.emit("bad", f"{p.short} didn't accept that key. Check that you copied all of it.")
            return
        if resp.status_code == 429:
            self.result.emit("warn", f"Your key works. {p.short} is busy right now, so replies may be slow.")
            return
        if resp.status_code >= 400:
            self.result.emit("error", f"{p.short} answered with an error ({resp.status_code}). Try again shortly.")
            return
        try:
            ids = [m.get("id", "") for m in resp.json().get("data", []) if isinstance(m, dict)]
        except ValueError:
            ids = []
        chat = chat_models(ids)
        if chat:
            self.models.emit(chat)
        if chat and self.model not in chat:
            self.result.emit("warn", f"Your key works, but {self.model} isn't in your {p.short} model list. "
                                     "Pick one in Settings > AI.")
        else:
            self.result.emit("ok", "Your key works. Pomo is ready to chat.")
