"""Ask Pomo with other providers: OpenAI, Gemini, Grok and OpenRouter (OpenAI-compatible endpoints)."""
import json
import tempfile
import unittest
from pathlib import Path

import httpx2
from PySide6.QtCore import QEventLoop, QTimer

from pomodoro import providers
from pomodoro.ai import CompatChatWorker, KeyCheckWorker, compat_tools, tool_results_messages
from pomodoro.config import Settings
from pomodoro.engine import TimerEngine
from pomodoro.storage import Storage


def sse(*chunks) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def delta(**d):
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def run_worker(provider_key, reply_bytes=None, status=200, tools=None):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        if status != 200:
            return httpx2.Response(status, json={"error": {"message": "nope"}})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=reply_bytes)

    worker = CompatChatWorker(provider_key, "test-key", "some-model", [{"role": "user", "content": "hi"}],
                              tools=tools, transport=httpx2.MockTransport(handler))
    texts, done, errors = [], [], []
    worker.text.connect(texts.append)
    worker.completed.connect(done.append)
    worker.failed.connect(errors.append)
    worker.run()  # synchronously, on this thread
    return seen, texts, done, errors


class CompatWorkerTests(unittest.TestCase):
    def test_streams_text(self):
        seen, texts, done, errors = run_worker("openai", sse(delta(role="assistant", content="Hi "),
                                                             delta(content="there!"), finish("stop")))
        self.assertEqual(errors, [])
        self.assertEqual("".join(texts), "Hi there!")
        self.assertEqual(seen["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(seen["headers"]["authorization"], "Bearer test-key")
        self.assertEqual(seen["body"]["messages"][0]["role"], "system")  # Pomo's persona goes first
        self.assertTrue(seen["body"]["stream"])
        reply = done[0]
        self.assertEqual((reply.text, reply.stop_reason, reply.tool_calls), ("Hi there!", "end_turn", []))
        self.assertEqual(reply.assistant_message, {"role": "assistant", "content": "Hi there!"})

    def test_tool_calls_arrive_in_pieces(self):
        call = {"index": 0, "id": "call_1", "type": "function", "function": {"name": "set_task", "arguments": ""}}
        _, _, done, errors = run_worker("xai", sse(
            delta(tool_calls=[call]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"task": '}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '"Thesis"}'}}]),
            finish("tool_calls")), tools=compat_tools("xai"))
        self.assertEqual(errors, [])
        reply = done[0]
        self.assertEqual(reply.stop_reason, "tool_use")
        self.assertEqual([(c.id, c.name, c.input) for c in reply.tool_calls], [("call_1", "set_task", {"task": "Thesis"})])
        self.assertEqual(reply.assistant_message["tool_calls"][0]["function"]["arguments"], '{"task": "Thesis"}')

    def test_whole_calls_without_an_index(self):  # how some providers send them
        calls = [{"id": "a", "function": {"name": "set_task", "arguments": '{"task": "A"}'}},
                 {"id": "b", "function": {"name": "start_session", "arguments": '{"kind": "focus", "minutes": 0}'}}]
        _, _, done, _ = run_worker("gemini", sse(delta(tool_calls=calls), finish("stop")))
        self.assertEqual([c.id for c in done[0].tool_calls], ["a", "b"])
        self.assertEqual(done[0].stop_reason, "tool_use")

    def test_bad_json_is_reported_not_run(self):
        call = {"index": 0, "id": "c", "function": {"name": "set_task", "arguments": '{"task": "x'}}
        _, _, done, _ = run_worker("openai", sse(delta(tool_calls=[call]), finish("length")))
        self.assertIsNone(done[0].tool_calls[0].input)
        self.assertFalse(done[0].complete)

    def test_friendly_errors(self):
        for status, words in ((401, "didn't accept your API key"), (404, "isn't available"), (429, "rate-limiting"),
                              (503, "having trouble")):
            with self.subTest(status=status):
                _, _, done, errors = run_worker("openrouter", status=status)
                self.assertEqual(done, [])
                self.assertIn(words, errors[0])

    def test_provider_endpoints_and_headers(self):
        seen, *_ = run_worker("openrouter", sse(finish("stop")))
        self.assertEqual(seen["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(seen["headers"]["x-title"], "Pomo")
        seen, *_ = run_worker("gemini", sse(finish("stop")))
        self.assertTrue(seen["url"].startswith("https://generativelanguage.googleapis.com/v1beta/openai/"))

    def test_tool_formats(self):
        openai = compat_tools("openai")[0]["function"]
        self.assertTrue(openai["strict"])
        self.assertFalse(openai["parameters"]["additionalProperties"])
        gemini = compat_tools("gemini")[0]["function"]
        self.assertNotIn("strict", gemini)
        self.assertNotIn("additionalProperties", json.dumps(gemini))
        self.assertEqual(tool_results_messages("openai", [("c1", "done", False), ("c2", "bad", True)]),
                         [{"role": "tool", "tool_call_id": "c1", "content": "done"},
                          {"role": "tool", "tool_call_id": "c2", "content": "Error: bad"}])
        self.assertEqual(tool_results_messages("anthropic", [("t1", "done", False)])[0]["content"][0]["tool_use_id"], "t1")


class CompatKeyCheckTests(unittest.TestCase):
    def check(self, provider_key, handler):
        worker = KeyCheckWorker(provider_key, "test-key", providers.default_model(provider_key),
                                transport=httpx2.MockTransport(handler))
        results, models = [], []
        worker.result.connect(lambda status, message: results.append(status))
        worker.models.connect(models.append)
        worker.run()
        return results, models

    def test_lists_chat_models(self):
        def handler(request):
            return httpx2.Response(200, json={"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.4-mini"},
                                                       {"id": "text-embedding-3-large"}, {"id": "tts-1"}]})
        results, models = self.check("openai", handler)
        self.assertEqual(results, ["ok"])
        self.assertEqual(models, [["gpt-5.4", "gpt-5.4-mini"]])

    def test_rejected_key(self):
        results, _ = self.check("xai", lambda request: httpx2.Response(401, json={"error": "bad key"}))
        self.assertEqual(results, ["bad"])

    def test_openrouter_checks_the_key_itself(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return httpx2.Response(401 if request.url.path.endswith("/key") else 200, json={"data": []})
        results, _ = self.check("openrouter", handler)
        self.assertEqual(results, ["bad"])
        self.assertEqual(paths, ["/api/v1/key"])

    def test_gemini_model_names(self):
        self.assertEqual(providers.chat_models(["models/gemini-3.5-flash", "models/text-embedding-004",
                                                "models/imagen-4"]), ["gemini-3.5-flash"])


class ProviderSettingsTests(unittest.TestCase):
    def test_models_per_provider(self):
        s = Settings()
        self.assertEqual(providers.model_for(s), "claude-opus-5-5")
        self.assertEqual(providers.model_for(s, "openai"), providers.default_model("openai"))
        providers.set_model(s, "openai", "gpt-5.4-mini")
        providers.set_model(s, "anthropic", "claude-haiku-5-5")
        s.ai_provider = "openai"
        self.assertEqual(providers.model_for(s), "gpt-5.4-mini")
        self.assertEqual(providers.model_for(s, "anthropic"), "claude-haiku-5-5")
        s.ai_provider = "bard"
        s.clamp()
        self.assertEqual(s.ai_provider, "anthropic")

    def test_key_files(self):
        base = Path("C:/data")
        self.assertEqual(providers.key_file(base, "anthropic").name, "ai_key.bin")  # unchanged, keys carry over
        self.assertEqual(providers.key_file(base, "gemini").name, "ai_key_gemini.bin")


class ChatWithOpenAITests(unittest.TestCase):
    """The tool loop through an OpenAI-compatible provider, end to end in the chat window."""

    def test_plan_with_openai(self):
        from PySide6.QtGui import QIcon

        import pomodoro.ai as ai
        from pomodoro.chat import ChatWindow
        from pomodoro.theme import theme_for

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = Storage(Path(tmp.name) / "history.db")
        self.addCleanup(storage.close)
        settings = Settings()
        settings.ai_provider = "openai"
        engine = TimerEngine(settings)

        class Ctrl:
            pass

        ctrl = Ctrl()
        ctrl.settings, ctrl.engine, ctrl.storage, ctrl.theme, ctrl.icon, ctrl.agents = (
            settings, engine, storage, theme_for("light"), QIcon(), None)
        ctrl.has_ai_key = lambda *a: True
        ctrl.ai_key = lambda *a: "test-key"
        ctrl.env_ai_key = lambda *a: None
        ctrl.pomo_listening = lambda: False
        ctrl.current_outfit = lambda: settings.pomo_outfit
        ctrl.save_settings = lambda: None

        def set_task(task):
            settings.remember_task(task)
            engine.task = settings.current_task
        ctrl.set_task = set_task
        ctrl.plan_changed = lambda: set_task(settings.task_queue[0]["task"]) if settings.task_queue else None
        ctrl.apply_settings = lambda new: (settings.update_from(new), engine.apply_settings())

        bodies = []
        replies = [
            sse(delta(tool_calls=[{"index": 0, "id": "call_1", "function": {
                "name": "start_session", "arguments": json.dumps({"kind": "focus", "minutes": 30})}}]),
                finish("tool_calls")),
            sse(delta(content="Started a 30-minute focus session. Go!"), finish("stop")),
        ]

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=replies[len(bodies) - 1])

        real = ai.CompatChatWorker.__init__

        def with_mock(self, *args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(handler)
            real(self, *args, **kwargs)
        ai.CompatChatWorker.__init__ = with_mock
        self.addCleanup(setattr, ai.CompatChatWorker, "__init__", real)

        chat = ChatWindow(ctrl)
        self.addCleanup(chat.deleteLater)
        chat.send("30 minutes of focus please")
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if chat.worker is None and chat._continue is None else None)
        poll.start(50)
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(10000)
        loop.exec()
        poll.stop()
        guard.stop()

        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0]["model"], providers.default_model("openai"))
        self.assertEqual([m["role"] for m in bodies[1]["messages"]], ["system", "user", "system", "assistant", "tool"])
        self.assertEqual(bodies[1]["messages"][-1]["tool_call_id"], "call_1")
        self.assertTrue(engine.is_running)
        self.assertEqual(round(engine.total), 30 * 60)


if __name__ == "__main__":
    unittest.main()
