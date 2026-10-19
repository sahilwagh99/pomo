"""User settings, persisted as JSON in the per-user data folder."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_NAME

SOUND_NAMES = ("Chime", "Bell", "Digital", "Soft")
ORB_SIZES = {"Small": 150, "Medium": 190, "Large": 230}


def default_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


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
    theme: str = "dark"
    orb_size: int = 190
    opacity: int = 100
    fade_when_away: bool = True
    always_on_top: bool = True
    show_task: bool = True
    focus_color: str = ""  # "" = theme default
    short_color: str = ""
    long_color: str = ""

    # General
    start_with_windows: bool = False
    dashboard_range: int = 7  # 0 = all time

    # Runtime state that survives restarts
    orb_x: int | None = None
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
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
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
    return False
