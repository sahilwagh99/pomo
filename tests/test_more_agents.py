"""More coding agents (Codex CLI, Gemini CLI) and the agent review queue."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from pomodoro import agent_hooks, claude_hooks, reviews
from pomodoro.agents import AgentHub
from pomodoro.config import Settings
from pomodoro.hook import MARKER, agent_of, decision_output, patch_files, summarize
from pomodoro.pacing import AgentPacer
from pomodoro.storage import Storage

from .test_agents import ROOT, settle, wait_for

PATCH = """*** Begin Patch
*** Update File: src/app.py
@@ def main():
-    print("hi")
+    print("hello")
+    return 0
*** Add File: src/new.py
+x = 1
*** End Patch"""


class TranslationTests(unittest.TestCase):
    def test_codex_patch_counts_every_file(self):
        self.assertEqual(patch_files(PATCH), [["src/app.py", 2, 1], ["src/new.py", 1, 0]])
        msg = summarize({"hook_event_name": "PostToolUse", "session_id": "c1", "cwd": r"C:\code\api",
                         "tool_name": "apply_patch", "tool_input": {"command": PATCH}}, agent="codex")
        self.assertEqual((msg["agent"], msg["tool"], msg["detail"]), ("codex", "Edit", "src/app.py and 1 more"))
        self.assertEqual((msg["added"], msg["removed"]), (3, 1))
        self.assertEqual(msg["files"], [["src/app.py", 2, 1], ["src/new.py", 1, 0]])

    def test_codex_commands_and_turns(self):
        run = summarize({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "tool_input": {"command": "npm test"}}, agent="codex")
        self.assertEqual((run["tool"], run["detail"]), ("Bash", "npm test"))
        stop = summarize({"hook_event_name": "Stop", "last_assistant_message": "All green."}, answer=True,
                         agent="codex")
        self.assertTrue(stop["wait"])
        interrupted = summarize({"hook_event_name": "Interrupt"}, answer=True, agent="codex")
        self.assertEqual(interrupted["event"], "Stop")
        self.assertFalse(interrupted["wait"])  # nothing to reply to

    def test_gemini_events_become_claude_events(self):
        prompt = summarize({"hook_event_name": "BeforeAgent", "prompt": "Add a login page"}, agent="gemini")
        self.assertEqual((prompt["event"], prompt["detail"]), ("UserPromptSubmit", "Add a login page"))
        edit = summarize({"hook_event_name": "AfterTool", "tool_name": "replace",
                          "tool_input": {"file_path": r"C:\code\web\src\login.tsx", "old_string": "a",
                                         "new_string": "a\nb"}}, agent="gemini")
        self.assertEqual((edit["event"], edit["tool"], edit["detail"]), ("PostToolUse", "Edit", "src/login.tsx"))
        self.assertEqual(edit["files"], [["src/login.tsx", 2, 1]])
        done = summarize({"hook_event_name": "AfterAgent", "prompt_response": "Done!", "stop_hook_active": False},
                         answer=True, agent="gemini")
        self.assertEqual((done["event"], done["message"], done["wait"]), ("Stop", "Done!", True))
        ask = summarize({"hook_event_name": "Notification", "notification_type": "ToolPermission",
                         "message": "Allow run_shell_command?"}, agent="gemini")
        self.assertEqual(ask["kind"], "permission_prompt")
        self.assertFalse(summarize({"hook_event_name": "BeforeTool", "tool_name": "AskUserQuestion"}, answer=True,
                                   agent="gemini")["wait"])  # questions are Claude Code's

    def test_replies_in_each_agents_words(self):
        reply = {"decision": "reply", "message": "Add tests too"}
        self.assertEqual(decision_output(reply, {"hook_event_name": "Stop"}, "codex"),
                         {"decision": "block", "reason": "The user replied (from Pomo): Add tests too"})
        self.assertEqual(decision_output(reply, {"hook_event_name": "AfterAgent"}, "gemini")["decision"], "deny")
        allow = decision_output({"decision": "allow"}, {"hook_event_name": "PermissionRequest"}, "codex")
        self.assertEqual(allow["hookSpecificOutput"]["decision"], {"behavior": "allow"})

    def test_agent_flag(self):
        self.assertEqual(agent_of(["Pomo.exe", MARKER, "--agent", "codex"]), "codex")
        self.assertEqual(agent_of(["Pomo.exe", MARKER, "--agent", "nope"]), "claude")
        self.assertEqual(agent_of(["Pomo.exe", MARKER]), "claude")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def test_codex_hooks_file(self):
        path = Path(self.dir.name) / "hooks.json"
        mine = {"type": "command", "command": "python mine.py"}
        path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}}), encoding="utf-8")
        self.assertEqual(agent_hooks.status("codex", path), "off")
        agent_hooks.install("codex", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(agent_hooks.status("codex", path), "connected")
        self.assertIn(mine, data["hooks"]["Stop"][0]["hooks"])  # yours stay
        ours = [h for g in data["hooks"]["Stop"] for h in g["hooks"] if MARKER in h["command"]]
        self.assertEqual(len(ours), 1)
        self.assertIn("--agent codex --answer", ours[0]["command"])
        self.assertTrue(data["hooks"]["PostToolUse"][0]["hooks"][0]["async"])  # watching never slows Codex down
        self.assertTrue(path.with_name("hooks.json.pomo-backup").exists())
        agent_hooks.uninstall("codex", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"hooks": {"Stop": [{"hooks": [mine]}]}})

    def test_gemini_settings_keep_everything_else(self):
        path = Path(self.dir.name) / "settings.json"
        path.write_text(json.dumps({"theme": "GitHub", "hooks": {}}), encoding="utf-8")
        agent_hooks.install("gemini", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "GitHub")
        self.assertEqual(data["hooks"]["AfterTool"][0]["matcher"], agent_hooks.GEMINI_WATCHED_TOOLS)
        self.assertEqual(data["hooks"]["AfterAgent"][0]["hooks"][0]["timeout"], 330_000)  # milliseconds
        self.assertNotIn("BeforeTool", data["hooks"])  # Gemini waits on every hook: Pomo skips this one
        self.assertEqual(agent_hooks.status("gemini", path), "connected")
        agent_hooks.uninstall("gemini", path)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"theme": "GitHub"})

    def test_a_broken_file_is_left_alone(self):
        path = Path(self.dir.name) / "settings.json"
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(claude_hooks.HookConfigError):
            agent_hooks.install("gemini", path)
        self.assertEqual(path.read_text(encoding="utf-8"), "{ not json")

    def test_commands_survive_any_shell(self):
        original = agent_hooks.hook_command
        try:
            agent_hooks.hook_command = lambda: (r"C:\Apps\Pomo\Pomo.exe", [MARKER])
            self.assertEqual(agent_hooks.command_line("codex", answer=True),
                             r"C:\Apps\Pomo\Pomo.exe --pomo-hook --agent codex --answer")
            agent_hooks.hook_command = lambda: (r"C:\No Such Place\Pomo.exe", [MARKER])  # no short name to use
            self.assertEqual(agent_hooks.command_line("gemini"), r'"C:\No Such Place\Pomo.exe" --pomo-hook --agent gemini')
        finally:
            agent_hooks.hook_command = original


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")
        self.settings = Settings()
        self.ctrl = SimpleNamespace(settings=self.settings, storage=self.storage)
        self.hub = AgentHub(self.ctrl)
        self.hub.user_at_terminal = lambda: True
        self.reviews = []
        self.hub.review_ready.connect(self.reviews.append)

    def tearDown(self):
        self.hub.shutdown()
        settle()
        self.storage.close()
        self.dir.cleanup()

    def send(self, event: dict, agent: str = "claude", **extra):
        msg = summarize({"session_id": "s1", "cwd": r"C:\code\api", **event}, agent=agent)
        msg.update(extra)
        self.hub.handle(msg)

    def test_a_turn_that_changed_files_becomes_a_review(self):
        self.send({"hook_event_name": "UserPromptSubmit", "prompt": "fix the login"})
        self.send({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                   "tool_input": {"file_path": r"C:\code\api\auth\login.py", "old_string": "a", "new_string": "a\nb"}})
        self.send({"hook_event_name": "PostToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": r"C:\code\api\tests\test_login.py", "content": "x\ny\nz"}})
        self.send({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "pytest"}})
        self.send({"hook_event_name": "Stop", "last_assistant_message": "Fixed."})
        [review] = self.reviews
        self.assertEqual(review["project"], "api")
        self.assertEqual(sorted(review["files"]), ["auth/login.py", "tests/test_login.py"])
        self.assertEqual((review["added"], review["removed"]), (5, 1))
        self.send({"hook_event_name": "Stop", "last_assistant_message": "Anything else?"})
        self.assertEqual(len(self.reviews), 1)  # nothing new changed

    def test_codex_patches_count_too(self):
        self.send({"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "tool_input": {"command": PATCH}},
                  agent="codex")
        events = [e for e in self.storage.agent_events() if e.event == "PostToolUse"]
        self.assertEqual([e.detail for e in events], ["src/app.py", "src/new.py"])  # one per file
        self.send({"hook_event_name": "Stop", "last_assistant_message": "Patched."}, agent="codex")
        self.assertEqual(self.reviews[0]["agent"], "codex")
        self.assertEqual(self.hub.sessions["s1"].agent_label, "Codex")

    def test_turned_off(self):
        self.settings.agent_reviews = False
        self.send({"hook_event_name": "PostToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": r"C:\code\api\x.py", "content": "x"}})
        self.send({"hook_event_name": "Stop"})
        self.assertEqual(self.reviews, [])

    def test_the_queue(self):
        s = Settings()
        first = reviews.add(s, {"project": "api", "agent": "claude", "files": ["a/x.py", "a/y.py"], "added": 10,
                                "removed": 2})
        self.assertEqual(reviews.title(first), "Review Claude's changes in api")
        reviews.add(s, {"project": "web", "agent": "codex", "files": ["ui.tsx"], "added": 1, "removed": 0})
        merged = reviews.add(s, {"project": "api", "agent": "claude", "files": ["a/z.py", "a/x.py"], "added": 5,
                                 "removed": 1})
        self.assertEqual(len(s.reviews), 2)
        self.assertIs(reviews.latest(s), merged)  # the newest goes last
        self.assertEqual(merged["files"], ["a/z.py", "a/x.py", "a/y.py"])
        self.assertEqual(reviews.lines_text(merged), "+15 / -3 lines")
        self.assertEqual(reviews.summary(merged), "3 files (+15 / -3 lines): z.py, x.py and y.py")
        self.assertIs(reviews.find(s, "Review Claude's changes in api"), merged)
        reviews.remove(s, merged)
        self.assertEqual([r["project"] for r in s.reviews], ["web"])
        many = {"project": "big", "files": [f"f{i}.py" for i in range(7)]}
        self.assertEqual(reviews.files_text(many), "f0.py, f1.py, f2.py, f3.py and 3 more")
        old = datetime.now() - timedelta(hours=1)
        self.assertEqual([r["project"] for r in reviews.since(s, old)], ["web"])

    def test_settings_keep_the_queue_tidy(self):
        s = Settings()
        s.reviews = [{"project": "api", "files": []}, "junk", {"files": []}] + \
                    [{"project": f"p{i}", "files": []} for i in range(12)]
        s.clamp()
        self.assertEqual(len(s.reviews), 10)
        self.assertTrue(all(isinstance(r, dict) and "project" in r for r in s.reviews))


class RoundTripTests(unittest.TestCase):
    """The real hook client, run the way Codex and Gemini run it, talking to a live hub."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")
        self.ctrl = SimpleNamespace(settings=Settings(), storage=self.storage)
        self.hub = AgentHub(self.ctrl)
        self.hub.user_at_terminal = lambda: False
        self.asked = []
        self.hub.approval_requested.connect(self.asked.append)

    def tearDown(self):
        self.hub.shutdown()
        settle()
        self.storage.close()
        self.dir.cleanup()

    def run_hook(self, event: dict, agent: str, answer: bool = True):
        args = [sys.executable, str(ROOT / "main.py"), MARKER, "--agent", agent] + (["--answer"] if answer else [])
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=os.environ.copy())
        proc.stdin.write(json.dumps(event).encode())
        proc.stdin.close()
        return proc

    def finish(self, proc) -> str:
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        out = proc.stdout.read().decode()
        proc.stdout.close()
        proc.stderr.close()
        return out

    def test_reply_to_codex(self):
        proc = self.run_hook({"hook_event_name": "Stop", "session_id": "cx", "cwd": r"C:\code\api",
                              "last_assistant_message": "Done. Shall I deploy?", "stop_hook_active": False}, "codex")
        self.assertTrue(wait_for(lambda: bool(self.asked)))
        self.assertEqual(self.asked[0].session.agent, "codex")
        self.hub.resolve(self.asked[0].id, "reply", "Not yet")
        out = json.loads(self.finish(proc))
        self.assertEqual(out, {"decision": "block", "reason": "The user replied (from Pomo): Not yet"})

    def test_codex_permission(self):
        proc = self.run_hook({"hook_event_name": "PermissionRequest", "session_id": "cx", "cwd": r"C:\code\api",
                              "tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}, "codex", answer=False)
        self.assertTrue(wait_for(lambda: bool(self.asked)))
        self.assertEqual(self.asked[0].detail, "rm -rf build")
        self.hub.resolve(self.asked[0].id, "deny", "Keep the build folder")
        out = json.loads(self.finish(proc))
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_reply_to_gemini(self):
        proc = self.run_hook({"hook_event_name": "AfterAgent", "session_id": "gm", "cwd": r"C:\code\web",
                              "prompt": "add a page", "prompt_response": "Added /about.", "stop_hook_active": False},
                             "gemini")
        self.assertTrue(wait_for(lambda: bool(self.asked)))
        self.hub.resolve(self.asked[0].id, "reply", "Now add a footer")
        out = json.loads(self.finish(proc))
        self.assertEqual(out["decision"], "deny")  # Gemini's word for "keep going with this"
        self.assertIn("Now add a footer", out["reason"])


class StartReviewTests(unittest.TestCase):
    """'Review next' puts the review right after the session you're in, whatever your plan holds."""

    def setUp(self):
        from pomodoro.engine import TimerEngine

        self.settings = Settings()
        self.engine = TimerEngine(self.settings)
        tasks = []
        self.fake = SimpleNamespace(engine=self.engine, settings=self.settings, save_settings=lambda: None,
                                    view=SimpleNamespace(isVisible=lambda: False), tasks=tasks)
        self.fake.set_task = lambda t: (tasks.append(t), setattr(self.engine, "task", t))
        self.fake.plan_changed = lambda: self.fake.set_task(self.settings.task_queue[0]["task"])
        self.review = {"project": "api", "agent": "codex", "files": ["a.py"], "added": 3, "removed": 1}

    def start(self):
        from pomodoro.app import PomoApp

        PomoApp.start_review(self.fake, self.review)

    def test_mid_session_with_a_plan(self):
        self.settings.task_queue = [{"task": "Thesis", "pomodoros": 3}, {"task": "Email", "pomodoros": 1}]
        self.engine.task = "Thesis"
        self.engine.start()
        self.start()
        self.assertEqual([(t["task"], t["pomodoros"]) for t in self.settings.task_queue],
                         [("Thesis", 1), ("Review Codex's changes in api", 1), ("Thesis", 2), ("Email", 1)])
        self.assertEqual(self.engine.task, "Thesis")  # the session you're in keeps its name

    def test_mid_session_without_a_plan(self):
        self.engine.task = "Thesis"
        self.engine.start()
        self.start()
        self.assertEqual([t["task"] for t in self.settings.task_queue], ["Thesis", "Review Codex's changes in api"])

    def test_between_sessions(self):
        self.start()
        self.assertEqual(self.engine.task, "Review Codex's changes in api")
        self.assertEqual(self.settings.task_queue, [])


class PacingNewsTests(unittest.TestCase):
    def test_review_news_on_the_break_card(self):
        text = AgentPacer.summary([("review", "api", "+12 / -3 lines"), ("finished", "api", "4:12"),
                                   ("waiting", "web", "")])
        self.assertEqual(text, "While you focused: api has changes for you to review (+12 / -3 lines), "
                               "web is waiting for you.")


if __name__ == "__main__":
    unittest.main()
