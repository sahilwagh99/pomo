import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

os.environ["POMO_AGENT_PIPE"] = f"Pomo-agents-test-{os.getpid()}"

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from pomodoro import claude_hooks
from pomodoro.agents import AgentHub, activity_text
from pomodoro.config import Settings
from pomodoro.hook import MARKER, decision_output, summarize
from pomodoro.stats import summarize_agents
from pomodoro.storage import Storage

ROOT = Path(__file__).resolve().parent.parent
_app = QCoreApplication.instance() or QCoreApplication([])


def wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    loop = QEventLoop()
    while not predicate() and time.monotonic() < deadline:
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


class HookClientTests(unittest.TestCase):
    def test_summarize_trims_and_counts_edits(self):
        msg = summarize({
            "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": r"C:\code\pomo",
            "tool_name": "Edit",
            "tool_input": {"file_path": r"C:\code\pomo\pomodoro\app.py", "old_string": "a\nb", "new_string": "a\nb\nc"},
        })
        self.assertEqual(msg["detail"], "pomodoro/app.py")
        self.assertEqual((msg["added"], msg["removed"]), (3, 2))
        self.assertFalse(msg["wait"])
        self.assertNotIn("tool_input", msg)

    def test_permission_request_waits_and_keeps_full_command(self):
        command = "npm run build && " + "x" * 500
        msg = summarize({"hook_event_name": "PermissionRequest", "tool_name": "Bash",
                         "tool_input": {"command": command}})
        self.assertTrue(msg["wait"])
        self.assertEqual(msg["detail"], command)

    def test_decision_output_shapes(self):
        allow = decision_output({"decision": "allow"})
        self.assertEqual(allow["hookSpecificOutput"]["decision"], {"behavior": "allow"})
        self.assertEqual(allow["hookSpecificOutput"]["hookEventName"], "PermissionRequest")
        deny = decision_output({"decision": "deny", "message": "no"})["hookSpecificOutput"]["decision"]
        self.assertEqual((deny["behavior"], deny["message"], deny["interrupt"]), ("deny", "no", False))
        self.assertIsNone(decision_output({"decision": "pass"}))
        self.assertIsNone(decision_output({}))


class ClaudeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_install_keeps_user_hooks_and_uninstall_removes_only_ours(self):
        mine = {"type": "command", "command": "echo mine"}
        self.path.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [mine]}]}}))
        claude_hooks.install(self.path)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["model"], "opus")
        stop_handlers = [h for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn(mine, stop_handlers)
        self.assertEqual(claude_hooks.status(self.path), "connected")
        approval = data["hooks"]["PermissionRequest"][0]
        self.assertEqual(approval["matcher"], "*")
        self.assertIn(MARKER, approval["hooks"][0]["args"])
        self.assertNotIn("async", approval["hooks"][0])  # approvals must block
        self.assertTrue(data["hooks"]["PreToolUse"][0]["hooks"][0]["async"])
        self.assertTrue(self.path.with_name("settings.json.pomo-backup").exists())

        claude_hooks.install(self.path)  # idempotent
        data = json.loads(self.path.read_text())
        self.assertEqual(len(data["hooks"]["PermissionRequest"]), 1)

        claude_hooks.uninstall(self.path)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["hooks"], {"Stop": [{"hooks": [mine]}]})
        self.assertEqual(claude_hooks.status(self.path), "off")

    def test_never_overwrites_a_broken_file(self):
        self.path.write_text("{ not json")
        with self.assertRaises(claude_hooks.HookConfigError):
            claude_hooks.install(self.path)
        self.assertEqual(self.path.read_text(), "{ not json")


class AgentHubTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")
        self.ctrl = SimpleNamespace(settings=Settings(), storage=self.storage)
        self.hub = AgentHub(self.ctrl)

    def tearDown(self):
        self.hub.shutdown()
        self.storage.close()
        self.dir.cleanup()

    def send(self, event, **extra):
        self.hub.handle({"v": 1, "event": event, "session": "s1", "cwd": r"C:\code\pomo", **extra})

    def test_session_lifecycle_and_signal(self):
        finished = []
        self.hub.turn_finished.connect(lambda s, secs: finished.append(s.project))
        self.send("SessionStart", kind="startup")
        self.assertIsNone(self.hub.signal())
        self.send("UserPromptSubmit", detail="add a dark mode")
        self.send("PreToolUse", tool="Bash", detail="npm test")
        self.assertEqual(self.hub.signal(), "busy")
        self.assertEqual(self.hub.headline(), "pomo: Running npm test")
        self.send("PostToolUse", tool="Edit", detail="src/app.py", added=4, removed=1)
        self.send("Stop", detail="Done.")
        self.assertEqual(finished, ["pomo"])
        self.assertIsNone(self.hub.signal())
        sm = summarize_agents(self.storage.agent_events(since=date.today()), date.today())
        self.assertEqual((sm.sessions, sm.prompts, sm.tool_calls, sm.files_edited, sm.turns), (1, 1, 1, 1, 1))
        self.assertEqual((sm.lines_added, sm.lines_removed), (4, 1))
        self.send("SessionEnd", kind="other")
        self.assertEqual(self.hub.sessions, {})

    def test_approvals_disabled_answers_immediately(self):
        self.ctrl.settings.agent_approvals = False
        asked = []
        self.hub.approval_requested.connect(asked.append)
        self.send("PermissionRequest", tool="Bash", detail="rm -rf build")
        self.assertEqual(asked, [])
        self.assertEqual(self.hub.pending, {})

    def test_activity_text(self):
        self.assertEqual(activity_text("Edit", "app.py"), "Editing app.py")
        self.assertEqual(activity_text("TodoWrite", "x"), "Planning")
        self.assertEqual(activity_text("Mystery", ""), "Using Mystery")


class HookRoundTripTests(unittest.TestCase):
    """The real hook client talking to a live hub over the named pipe."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")
        self.ctrl = SimpleNamespace(settings=Settings(), storage=self.storage)
        self.hub = AgentHub(self.ctrl)
        self.assertTrue(self.hub.listening)

    def tearDown(self):
        self.hub.shutdown()
        self.storage.close()
        self.dir.cleanup()

    def run_hook(self, event: dict) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, str(ROOT / "main.py"), MARKER], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
        proc.stdin.write(json.dumps(event).encode())
        proc.stdin.close()
        return proc

    def finish(self, proc):
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        out = proc.stdout.read().decode()
        proc.stdout.close()
        proc.stderr.close()
        return proc.returncode, out

    def test_observed_event_reaches_hub(self):
        proc = self.run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "abc",
                              "cwd": r"C:\code\demo", "prompt": "hello"})
        self.assertTrue(wait_for(lambda: "abc" in self.hub.sessions))
        self.assertEqual(self.hub.sessions["abc"].project, "demo")
        self.assertEqual(self.finish(proc), (0, ""))

    def test_approval_allow_round_trip(self):
        asked = []
        self.hub.approval_requested.connect(asked.append)
        proc = self.run_hook({"hook_event_name": "PermissionRequest", "session_id": "abc",
                              "cwd": r"C:\code\demo", "tool_name": "Bash", "tool_input": {"command": "npm test"}})
        self.assertTrue(wait_for(lambda: bool(asked)))
        self.assertEqual(asked[0].detail, "npm test")
        self.hub.resolve(asked[0].id, "allow")
        code, out = self.finish(proc)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_approval_in_terminal_prints_nothing(self):
        asked = []
        self.hub.approval_requested.connect(asked.append)
        proc = self.run_hook({"hook_event_name": "PermissionRequest", "session_id": "abc",
                              "tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "x"}})
        self.assertTrue(wait_for(lambda: bool(asked)))
        self.hub.resolve(asked[0].id, "terminal")
        self.assertEqual(self.finish(proc), (0, ""))

    def test_hook_is_silent_when_pomo_is_not_running(self):
        self.hub.shutdown()
        proc = self.run_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash",
                              "tool_input": {"command": "ls"}})
        self.assertEqual(self.finish(proc), (0, ""))



class RenameMigrationTests(unittest.TestCase):
    def test_hooks_from_focusorb_are_recognised_and_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            old = {"type": "command", "command": r"C:\old\FocusOrb.exe", "args": ["--focusorb-hook"], "async": True}
            mine = {"type": "command", "command": "echo mine"}
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [old]}, {"hooks": [mine]}]}}))
            self.assertEqual(claude_hooks.status(path), "outdated")
            self.assertTrue(claude_hooks.refresh(path))
            self.assertEqual(claude_hooks.status(path), "connected")
            data = json.loads(path.read_text())
            handlers = [h for g in data["hooks"]["Stop"] for h in g["hooks"]]
            self.assertNotIn(old, handlers)
            self.assertIn(mine, handlers)
            self.assertFalse(claude_hooks.refresh(path))  # nothing left to do

    def test_refresh_leaves_unconnected_users_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"model": "opus"}')
            self.assertFalse(claude_hooks.refresh(path))
            self.assertEqual(json.loads(path.read_text()), {"model": "opus"})

    def test_data_folder_moves_from_focusorb_once(self):
        from pomodoro import config

        with tempfile.TemporaryDirectory() as tmp:
            old_appdata = os.environ.get("APPDATA")
            os.environ["APPDATA"] = tmp
            try:
                legacy = Path(tmp) / "FocusOrb"
                (legacy / "cache").mkdir(parents=True)
                (legacy / "settings.json").write_text('{"daily_goal": 6}')
                (legacy / "history.db").write_bytes(b"db")
                self.assertTrue(config.migrate_legacy_data())
                new = Path(tmp) / "Pomo"
                # settings carry over, and Pomo opens as the island with a hello, like a new install
                self.assertEqual(json.loads((new / "settings.json").read_text()),
                                 {"daily_goal": 6, "display_mode": "island", "island_autohide": False, "welcomed": False})
                self.assertTrue((new / "history.db").exists())
                self.assertFalse((new / "cache").exists())
                self.assertTrue(legacy.exists())  # kept as a backup
                self.assertFalse(config.migrate_legacy_data())  # only once
            finally:
                if old_appdata is None:
                    del os.environ["APPDATA"]
                else:
                    os.environ["APPDATA"] = old_appdata


if __name__ == "__main__":
    unittest.main()
