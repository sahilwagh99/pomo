"""User settings, persisted as JSON in the per-user data folder."""
from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_NAME, LEGACY_NAME
from .providers import PROVIDERS, default_model

AI_MODELS = dict(PROVIDERS["anthropic"].models)  # Claude model id -> label

SOUND_NAMES = ("Chime", "Bell", "Digital", "Soft")
OUTFITS = {  # Pomo's wardrobe: key -> label
    "blossom": "Sakura blossom",
    "headphones": "Headphones",
    "beanie": "Cozy beanie",
    "party": "Party hat",
    "crown": "Crown",
    "bow": "Ribbon bow",
    "glasses": "Round glasses",
    "none": "Nothing",
    "hardhat": "Hard hat",
    "nightcap": "Nightcap",
    "headband": "Sweatband",
    "sunhat": "Sun hat",
    "chefhat": "Chef's hat",
    "beret": "Beret",
    # earned in the focus garden
    "catears": "Cat ears",
    "flowercrown": "Flower crown",
    "gradcap": "Graduation cap",
    "wizard": "Wizard hat",
}
UNLOCKS = {"catears": 10, "flowercrown": 25, "gradcap": 50, "wizard": 100}  # outfit -> lifetime pomodoros
ORB_SIZES = {"Small": 150, "Medium": 190, "Large": 230}
THEME_KEYS = ("light", "sakura", "lavender", "peach", "mint", "dark", "blueberry", "plum")  # see theme.THEMES
DEFAULT_DISTRACTING = ("youtube", "netflix", "twitch", "tiktok", "instagram", "facebook", "reddit", "twitter",
                       "9gag", "prime video", "disney+", "steam")
HOTKEYS = {  # action -> (setting, label)
    "toggle": ("hotkey_toggle", "Start or pause the timer"),
    "skip": ("hotkey_skip", "Skip to the next session"),
    "park": ("hotkey_park", "Park a thought"),
    "music": ("hotkey_music", "Play or pause music"),
    "show": ("hotkey_show", "Show or hide the timer"),
}


def _appdata_root() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / ".config"


def default_data_dir() -> Path:
    return _appdata_root() / APP_NAME


def migrate_legacy_data() -> bool:
    """Copy the data folder of FocusOrb (this app before 1.2) to Pomo's, once.

    The old folder is left in place as a backup. Returns True if anything was copied.
    """
    new, old = default_data_dir(), _appdata_root() / LEGACY_NAME
    if new.exists() or not old.is_dir():
        return False
    shutil.copytree(old, new, ignore=shutil.ignore_patterns("cache"))
    # Pomo is a fresh start: open as the island and say hello once, like a new install
    settings_file = new / "settings.json"
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.update(display_mode="island", island_autohide=False, welcomed=False)
            settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass
    return True


@dataclass
class Settings:
    # Timer
    focus_min: int = 25
    short_min: int = 5
    long_min: int = 15
    sessions_before_long: int = 4
    daily_goal: int = 8
    auto_start_breaks: bool = True
    auto_start_focus: bool = False

    # Alerts
    desktop_notifications: bool = True
    popup_card: bool = True
    sound_enabled: bool = True
    sound_name: str = "Chime"
    volume: int = 70
    sound_repeat: int = 2
    idle_reminder_min: int = 0  # 0 = off

    # Appearance
    theme: str = "light"  # light (Matcha), sakura, lavender, peach, mint, dark (Cocoa), blueberry, plum
    display_mode: str = "island"  # "island" (pill at the top of the screen, the first impression) | "orb"
    show_buddy: bool = True    # Pomo, the focus buddy
    pomo_outfit: str = "blossom"
    outfit_follows: bool = True  # Pomo dresses for what you're doing (sweatband in focus, sun hat on breaks...)
    treats: int = 0          # earned one per finished focus session, fed by clicking the dango
    treats_fed: int = 0
    game_best: int = 0       # Petal Catch high score
    island_autohide: bool = False  # tuck into a sliver when idle (off: the pill stays visible)
    welcomed: bool = False          # the first-run welcome card has been shown
    orb_size: int = 190
    opacity: int = 100
    fade_when_away: bool = True
    always_on_top: bool = True
    show_task: bool = True
    focus_color: str = ""  # "" = theme default
    short_color: str = ""
    long_color: str = ""

    # Coding agents (Claude Code)
    agent_approvals: bool = True      # answer permission requests from Pomo
    approval_wait_s: int = 60         # then Claude Code asks in the terminal instead
    agent_notify_finish: bool = True
    agent_notify_after_s: int = 30    # only for turns at least this long
    agent_break_hint: bool = True     # suggest a break while an agent works on a long task
    agent_break_after_s: int = 120
    agent_questions: bool = True      # answer Claude's multiple-choice questions in Pomo
    agent_reply: bool = True          # reply to Claude in Pomo when a turn ends (while you're away from the terminal)
    agent_reply_wait_s: int = 45      # how long Claude waits for that reply

    # Focus helpers
    rate_sessions: bool = True         # ask "how was that?" after each focus session
    suggest_length: bool = True        # suggest a better focus length from those answers
    length_tip_date: str = ""          # last suggestion (at most one a week)
    away_after_min: int = 5            # pause when nobody has touched the PC this long (0 = off)
    focus_guard: bool = False          # notice distracting apps and sites during focus (opt-in)
    distracting: list[str] = field(default_factory=lambda: list(DEFAULT_DISTRACTING))
    quiet_focus: bool = True           # hold Pomo's non-urgent alerts until the break
    break_card: bool = True            # when a break starts: breathe, parked thoughts, play
    hotkeys_on: bool = True
    hotkey_toggle: str = "Ctrl+Alt+Shift+P"
    hotkey_skip: str = "Ctrl+Alt+Shift+K"
    hotkey_park: str = "Ctrl+Alt+Shift+N"
    hotkey_music: str = "Ctrl+Alt+Shift+M"
    hotkey_show: str = "Ctrl+Alt+Shift+H"

    # Music (whatever plays on this PC: Spotify, YouTube in a browser, ...)
    music_controls: bool = True   # show what's playing, with play/pause and skip
    pomo_listens: bool = True     # Pomo wears headphones and sways along while music plays
    music_follows: bool = False   # play while focusing, pause on breaks (opt-in: it touches your music)

    # AI (Ask Pomo): your own key, with the provider and model you pick
    ai_provider: str = "anthropic"    # anthropic | openai | gemini | xai | openrouter
    ai_model: str = "claude-opus-5-5"  # the Claude model
    ai_models: dict = field(default_factory=dict)  # other providers: provider -> model id
    review_hour: int = 18             # offer an end-of-day review from this hour (0 = off)
    review_date: str = ""             # last day it was offered

    # Plan: tasks Ask Pomo (or you) lined up, worked through one focus session at a time
    task_queue: list[dict] = field(default_factory=list)  # {"task": str, "pomodoros": int}

    # Focus garden
    unlocks_seen: list[str] = field(default_factory=list)  # outfits whose unlock was celebrated

    # General
    start_with_windows: bool = False
    dashboard_range: int = 7  # 0 = all time

    # Runtime state that survives restarts
    orb_cx: int | None = None  # center of the orb's circle, in screen coordinates
    orb_cy: int | None = None
    orb_x: int | None = None   # 1.0 stored the window corner; read once for migration
    orb_y: int | None = None
    current_task: str = ""
    recent_tasks: list[str] = field(default_factory=list)
    cycle_index: int = 0
    cycle_date: str = ""

    @classmethod
    def load(cls, path: Path) -> "Settings":
        settings = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if not isinstance(raw, dict):
            return settings
        for f in fields(cls):
            if f.name in raw and _compatible(getattr(settings, f.name), raw[f.name]):
                setattr(settings, f.name, raw[f.name])
        settings.clamp()
        return settings

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def copy(self) -> "Settings":
        return copy.deepcopy(self)

    def update_from(self, other: "Settings") -> None:
        for f in fields(self):
            setattr(self, f.name, copy.deepcopy(getattr(other, f.name)))

    def clamp(self) -> None:
        self.focus_min = _clip(self.focus_min, 1, 180)
        self.short_min = _clip(self.short_min, 1, 60)
        self.long_min = _clip(self.long_min, 1, 120)
        self.sessions_before_long = _clip(self.sessions_before_long, 1, 12)
        self.daily_goal = _clip(self.daily_goal, 1, 48)
        self.volume = _clip(self.volume, 0, 100)
        self.sound_repeat = _clip(self.sound_repeat, 1, 5)
        self.idle_reminder_min = _clip(self.idle_reminder_min, 0, 240)
        self.opacity = _clip(self.opacity, 30, 100)
        self.orb_size = _clip(self.orb_size, 130, 260)
        if self.sound_name not in SOUND_NAMES:
            self.sound_name = SOUND_NAMES[0]
        if self.theme not in THEME_KEYS:
            self.theme = "light"
        self.treats = _clip(self.treats, 0, 9)
        if self.pomo_outfit not in OUTFITS:
            self.pomo_outfit = "blossom"
        if self.display_mode not in ("orb", "island"):
            self.display_mode = "island"
        self.approval_wait_s = _clip(self.approval_wait_s, 15, 300)
        self.agent_notify_after_s = _clip(self.agent_notify_after_s, 0, 3600)
        self.agent_break_after_s = _clip(self.agent_break_after_s, 30, 1800)
        self.agent_reply_wait_s = _clip(self.agent_reply_wait_s, 10, 300)
        self.away_after_min = _clip(self.away_after_min, 0, 60)
        self.review_hour = _clip(self.review_hour, 0, 23)
        self.distracting = [w.strip().lower() for w in self.distracting if isinstance(w, str) and w.strip()][:40]
        self.task_queue = [{"task": str(t["task"]).strip()[:120], "pomodoros": _clip(t.get("pomodoros", 1), 1, 12)}
                           for t in self.task_queue
                           if isinstance(t, dict) and isinstance(t.get("task"), str) and t["task"].strip()
                           and isinstance(t.get("pomodoros", 1), int)][:20]
        self.unlocks_seen = [k for k in self.unlocks_seen if k in UNLOCKS]
        if self.ai_provider not in PROVIDERS:
            self.ai_provider = "anthropic"
        if not self.ai_model.strip():  # any model id may be typed in; just not an empty one
            self.ai_model = default_model("anthropic")
        self.ai_models = {k: v.strip() for k, v in self.ai_models.items()
                          if k in PROVIDERS and k != "anthropic" and isinstance(v, str) and v.strip()}
        if self.dashboard_range not in (0, 7, 30, 90):
            self.dashboard_range = 7
        self.recent_tasks = [t for t in self.recent_tasks if isinstance(t, str) and t.strip()][:12]

    def remember_task(self, task: str) -> None:
        task = task.strip()
        self.current_task = task
        if task:
            self.recent_tasks = [task] + [t for t in self.recent_tasks if t != task][:11]


def _clip(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _compatible(default, value) -> bool:
    if default is None:
        return value is None or (isinstance(value, int) and not isinstance(value, bool))
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return False
