"""Answering Claude Code from Pomo: multiple-choice questions, notes on a denial, and replies when a turn ends."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pomodoro import claude_hooks
from pomodoro.agents import AgentHub
from pomodoro.config import Settings
from pomodoro.hook import ANSWER_FLAG, MARKER, decision_output, summarize
from pomodoro.storage import Storage

from .test_agents import ROOT, settle, wait_for

QUESTION_EVENT = {
    "hook_event_name": "PreToolUse", "session_id": "abc", "cwd": r"C:\code\demo", "tool_name": "AskUserQuestion",
    "tool_input": {"questions": [{"question": "Which framework should the new page use?", "header": "Framework",
                                  "options": [{"label": "React", "description": "Like the rest of the app"},
                                              {"label": "Vue", "description": "Smaller bundle"}],
                                  "multiSelect": False}]},
}
STOP_EVENT = {"hook_event_name": "Stop", "session_id": "abc", "cwd": r"C:\code\demo", "stop_hook_active": False,
              "last_assistant_message": "Done! I added the page.\n\nShould I also write tests?"}


class HookMessageTests(unittest.TestCase):
    def test_only_the_answering_hook_waits(self):
        background = summarize(QUESTION_EVENT)
        self.assertFalse(background["wait"])
        answering = summarize(QUESTION_EVENT, answer=True)
        self.assertTrue(answering["wait"])
        self.assertEqual(answering["kind"], "question")
        self.assertEqual(answering["questions"][0]["options"][1], {"label": "Vue", "description": "Smaller bundle"})
        self.assertFalse(summarize({**QUESTION_EVENT, "tool_name": "Bash"}, answer=True)["wait"])

    def test_end_of_turn_keeps_claudes_words(self):
        msg = summarize(STOP_EVENT, answer=True)
        self.assertTrue(msg["wait"])
        self.assertEqual(msg["message"], "Done! I added the page.\n\nShould I also write tests?")  # line breaks kept
        note = summarize({"hook_event_name": "Notification", "message": "Claude needs your permission to use Bash",
                          "notification_type": "permission_prompt"})
        self.assertEqual(note["message"], "Claude needs your permission to use Bash")

    def test_answers_become_updated_input(self):
        out = decision_output({"decision": "answer", "answers": ["Vue"]}, QUESTION_EVENT)
        spec = out["hookSpecificOutput"]
        self.assertEqual((spec["hookEventName"], spec["permissionDecision"]), ("PreToolUse", "allow"))
        self.assertEqual(spec["updatedInput"]["questions"], QUESTION_EVENT["tool_input"]["questions"])  # echoed whole
        self.assertEqual(spec["updatedInput"]["answers"], {"Which framework should the new page use?": "Vue"})
        self.assertIsNone(decision_output({"decision": "pass"}, QUESTION_EVENT))
        self.assertIsNone(decision_output({"decision": "answer", "answers": [""]}, QUESTION_EVENT))

    def test_a_reply_continues_the_turn(self):
        out = decision_output({"decision": "reply", "message": "Yes, add tests too"}, STOP_EVENT)
        self.assertEqual(out, {"decision": "block", "reason": "The user replied (from Pomo): Yes, add tests too"})
        self.assertIsNone(decision_output({"decision": "pass"}, STOP_EVENT))
        self.assertIsNone(decision_output({"decision": "reply", "message": "  "}, STOP_EVENT))

    def test_deny_with_a_note(self):
        out = decision_output({"decision": "deny", "message": "Use pnpm, not npm"},
                              {"hook_event_name": "PermissionRequest"})
        self.assertEqual(out["hookSpecificOutput"]["decision"]["message"], "Use pnpm, not npm")


class HookLayoutTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_waiting_hooks_and_background_hooks(self):
        claude_hooks.install(self.path)
        hooks = json.loads(self.path.read_text())["hooks"]
        stop = [h for g in hooks["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop), 1)
        self.assertIn(ANSWER_FLAG, stop[0]["args"])
        self.assertNotIn("async", stop[0])
        questions = [g for g in hooks["PreToolUse"] if g.get("matcher") == "AskUserQuestion"]
        self.assertIn(ANSWER_FLAG, questions[0]["hooks"][0]["args"])
        observers = [g for g in hooks["PreToolUse"] if g.get("matcher") == "*"]
        self.assertTrue(observers[0]["hooks"][0]["async"])
        self.assertEqual(claude_hooks.status(self.path), "connected")

    def test_an_older_layout_is_outdated(self):
        command, args = claude_hooks.hook_command()
        old = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command, "args": args, "async": True}]}]}}
        self.path.write_text(json.dumps(old))
        self.assertEqual(claude_hooks.status(self.path), "outdated")
        self.assertTrue(claude_hooks.refresh(self.path))
        self.assertEqual(claude_hooks.status(self.path), "connected")


class ReplyRoundTripTests(unittest.TestCase):
    """The real hook client (run with --answer) talking to a live hub."""

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

    def run_hook(self, event):
        proc = subprocess.Popen([sys.executable, str(ROOT / "main.py"), MARKER, ANSWER_FLAG], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
        proc.stdin.write(json.dumps(event).encode())
        proc.stdin.close()
        return proc

    def finish(self, proc):
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        out = proc.stdout.read().decode()
        proc.stdout.close()
        proc.stderr.close()
        return out

    def test_answer_a_question(self):
        proc = self.run_hook(QUESTION_EVENT)
        self.assertTrue(wait_for(lambda: bool(self.asked)))
        question = self.asked[0]
        self.assertEqual(question.kind, "question")
        self.assertEqual(self.hub.signal(), "alert")
        self.hub.resolve(question.id, "answer", ["React"])
        out = json.loads(self.finish(proc))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["answers"],
                         {"Which framework should the new page use?": "React"})

    def test_reply_when_a_turn_ends(self):
        finished = []
        self.hub.turn_finished.connect(lambda session, secs: finished.append(self.hub.awaiting_reply(session)))
        proc = self.run_hook(STOP_EVENT)
        self.assertTrue(wait_for(lambda: bool(self.asked)))
        reply = self.asked[0]
        self.assertEqual((reply.kind, reply.message), ("reply", STOP_EVENT["last_assistant_message"]))
        self.assertEqual(finished, [True])
        self.assertIsNone(self.hub.signal())  # an optional reply isn't an alert
        self.hub.resolve(reply.id, "reply", "Yes please, add tests")
        out = json.loads(self.finish(proc))
        self.assertEqual(out["decision"], "block")
        self.assertIn("Yes please, add tests", out["reason"])
        self.assertEqual(self.hub.sessions["abc"].status, "working")

    def test_at_the_terminal_nothing_waits(self):
        self.hub.user_at_terminal = lambda: True
        proc = self.run_hook(STOP_EVENT)
        self.assertEqual(self.finish(proc), "")
        self.assertEqual(self.asked, [])
        self.assertTrue(wait_for(lambda: "abc" in self.hub.sessions))
        self.assertEqual(self.hub.sessions["abc"].message, STOP_EVENT["last_assistant_message"])

    def test_turned_off_passes_at_once(self):
        self.ctrl.settings.agent_reply = False
        self.ctrl.settings.agent_questions = False
        self.assertEqual(self.finish(self.run_hook(STOP_EVENT)), "")
        self.assertEqual(self.finish(self.run_hook(QUESTION_EVENT)), "")
        self.assertEqual(self.asked, [])

    def test_no_answer_in_time(self):
        self.ctrl.settings.agent_reply_wait_s = 10
        proc = self.run_hook(STOP_EVENT)
        self.assertTrue(wait_for(lambda: bool(self.asked)))
        self.hub.resolve(self.asked[0].id, "timeout")
        self.assertEqual(self.finish(proc), "")


if __name__ == "__main__":
    unittest.main()
