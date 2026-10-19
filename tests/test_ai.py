import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from pomodoro import keystore
from pomodoro.ai import SYSTEM_PROMPT, build_context, quick_prompt
from pomodoro.config import Settings
from pomodoro.engine import TimerEngine
from pomodoro.storage import AgentEvent, SessionRecord, Storage

_app = QCoreApplication.instance() or QCoreApplication([])


class KeystoreTests(unittest.TestCase):
    def test_round_trip_is_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai_key.bin"
            keystore.save_key(path, "  sk-ant-test-1234567890  ")
            self.assertNotIn(b"sk-ant-test", path.read_bytes())
            self.assertEqual(keystore.load_key(path), "sk-ant-test-1234567890")
            keystore.delete_key(path)
            self.assertIsNone(keystore.load_key(path))

    def test_mask(self):
        self.assertEqual(keystore.mask("sk-ant-api03-abcdefghijkl"), "sk-ant-…ijkl")


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        self.now = now
        self.storage.add_many([
            SessionRecord(now - timedelta(hours=2), now - timedelta(hours=2) + timedelta(minutes=25), "focus",
                          1500, 1500, True, "Thesis"),
            SessionRecord(now - timedelta(days=1), now - timedelta(days=1) + timedelta(minutes=25), "focus",
                          1500, 1500, True, "Reading"),
        ])
        self.storage.add_agent_event(AgentEvent(now, "s1", "pomo", "UserPromptSubmit", detail="add dark mode"))
        self.storage.add_agent_event(AgentEvent(now, "s1", "pomo", "PostToolUse", "Edit", "app.py", 3, 1))
        settings = Settings()
        engine = TimerEngine(settings)
        engine.task = "Thesis"
        self.ctrl = SimpleNamespace(settings=settings, engine=engine, storage=self.storage, agents=None)

    def tearDown(self):
        self.storage.close()
        self.dir.cleanup()

    def test_context_has_the_facts_pomo_needs(self):
        text = build_context(self.ctrl, now=self.now)
        self.assertIn("Current task: Thesis.", text)
        self.assertIn("Today: 1 of 8 pomodoros done", text)
        self.assertIn("Last 7 days: 2 pomodoros", text)
        self.assertIn("Coding agents today: 1 session(s), 1 prompt(s), 1 tool calls, 1 file(s) edited (+3/-1 lines)", text)
        self.assertIn("pomo: add dark mode", text)
        self.assertTrue(text.startswith("Pomo context"))

    def test_quick_prompts(self):
        self.assertIn('"Essay"', quick_prompt("plan", "Essay"))
        self.assertIn("Ask me what I'm working on", quick_prompt("plan", ""))
        for action in ("review", "agents", "pep"):
            self.assertTrue(quick_prompt(action, ""))

    def test_system_prompt_is_stable_text(self):
        # no timestamps or per-request data, so the prefix can be cached
        self.assertNotIn(str(self.now.year), SYSTEM_PROMPT)


SSE_REPLY = "".join(
    f"event: {name}\ndata: {data}\n\n" for name, data in [
        ("message_start", '{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant",'
                          '"model":"claude-opus-5-5","content":[],"stop_reason":null,"stop_sequence":null,'
                          '"usage":{"input_tokens":10,"output_tokens":1}}}'),
        ("content_block_start", '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}'),
        ("content_block_delta", '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi "}}'),
        ("content_block_delta", '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"there!"}}'),
        ("content_block_stop", '{"type":"content_block_stop","index":0}'),
        ("message_delta", '{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},'
                          '"usage":{"output_tokens":5}}'),
        ("message_stop", '{"type":"message_stop"}'),
    ]
)


class ChatWorkerTests(unittest.TestCase):
    """The real SDK request, against a mock transport (no network)."""

    def run_worker(self, model: str, status: int = 200):
        import json

        import anthropic
        import httpx2

        from pomodoro.ai import ChatWorker

        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            captured["headers"] = request.headers
            if status != 200:
                return httpx2.Response(status, json={"type": "error", "error": {"type": "authentication_error",
                                                                                 "message": "invalid x-api-key"}})
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=SSE_REPLY.encode())

        real = anthropic.Anthropic
        anthropic.Anthropic = lambda **kw: real(api_key="test-key", max_retries=0, http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)))
        try:
            messages = [{"role": "user", "content": "hello"}, {"role": "system", "content": "Pomo context: ..."}]
            worker = ChatWorker("test-key", model, messages)
            chunks, finals, errors = [], [], []
            worker.text.connect(chunks.append)
            worker.completed.connect(finals.append)
            worker.failed.connect(errors.append)
            worker.run()  # synchronously, on this thread
        finally:
            anthropic.Anthropic = real
        return captured, chunks, finals, errors

    def test_request_shape_and_streamed_reply(self):
        captured, chunks, finals, errors = self.run_worker("claude-opus-5-5")
        self.assertEqual(errors, [])
        self.assertEqual("".join(chunks), "Hi there!")
        self.assertEqual(finals[0].stop_reason, "end_turn")
        body = captured["body"]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", captured["headers"]["anthropic-beta"])
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertTrue(body["stream"])
        self.assertEqual([m["role"] for m in body["messages"]], ["user", "system"])
        self.assertNotIn("thinking", body)  # Opus 5.5 always thinks adaptively; no explicit config

    def test_haiku_has_no_fallbacks(self):
        captured, _, _, errors = self.run_worker("claude-haiku-5-5")
        self.assertEqual(errors, [])
        self.assertNotIn("fallbacks", captured["body"])

    def test_bad_key_gives_a_friendly_error(self):
        _, chunks, finals, errors = self.run_worker("claude-opus-5-5", status=401)
        self.assertEqual((chunks, finals), ([], []))
        self.assertIn("API key", errors[0])


class KeyCheckTests(unittest.TestCase):
    """Bring your own key: the free Models API lookup, against a mock transport (no network)."""

    def check(self, status: int | None):
        import anthropic
        import httpx2

        from pomodoro.ai import KeyCheckWorker

        paths = []

        def handler(request):
            paths.append((request.method, request.url.path))
            if status is None:
                raise httpx2.ConnectError("offline", request=request)
            if status != 200:
                kind = {401: "authentication_error", 404: "not_found_error"}.get(status, "api_error")
                return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": "nope"}})
            return httpx2.Response(200, json={"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
                                              "created_at": "2026-01-01T00:00:00Z"})

        real = anthropic.Anthropic
        anthropic.Anthropic = lambda **kw: real(api_key="test-key", max_retries=0, http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)))
        try:
            results = []
            worker = KeyCheckWorker("test-key", "claude-opus-5-5")
            worker.result.connect(lambda s, m: results.append((s, m)))
            worker.run()  # synchronously, on this thread
        finally:
            anthropic.Anthropic = real
        return paths, results

    def test_working_key_costs_nothing(self):
        paths, results = self.check(200)
        self.assertEqual(paths, [("GET", "/v1/models/claude-opus-5-5")])  # no tokens: just a model lookup
        self.assertEqual(results[0][0], "ok")

    def test_rejected_key(self):
        self.assertEqual(self.check(401)[1][0][0], "bad")

    def test_key_without_the_model_still_works(self):
        status, message = self.check(404)[1][0]
        self.assertEqual(status, "warn")
        self.assertIn("claude-opus-5-5", message)

    def test_offline_is_not_a_bad_key(self):
        self.assertEqual(self.check(None)[1][0][0], "error")

    def test_first_run_opens_as_the_island(self):
        s = Settings()
        self.assertEqual((s.display_mode, s.island_autohide, s.welcomed), ("island", False, False))
        s.display_mode = "bubble"
        s.clamp()
        self.assertEqual(s.display_mode, "island")


if __name__ == "__main__":
    unittest.main()
