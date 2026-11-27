"""The panel's Home tab (the timer, the clock and weather, quick controls and a quick note), and the list of tabs."""
from __future__ import annotations

from .panel import Page, register
from .panel_calendar import ClockCard
from .panel_pages import QuickNoteCard, TimerCard


class HomePage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.timer = self.add(TimerCard(ctrl, 300))
        self.clock = self.add(ClockCard(ctrl, 210, open_calendar=lambda: ctrl.open_panel(tab="calendar")))
        self.controls = None
        try:
            from .panel_system import ControlsCard

            self.controls = self.add(ControlsCard(ctrl))
        except ImportError:
            pass
        self.note = self.add(QuickNoteCard(ctrl), 1)

    def refresh(self) -> None:
        self.timer.tick()
        self.clock.refresh()
        if self.controls is not None:
            self.controls.refresh()

    def tick(self) -> None:
        self.timer.tick()
        self.clock.tick()


def register_all(ctrl) -> None:
    """Every tab, in the order they sit in the panel. A tab whose module isn't there is left out."""
    from .panel_calendar import CalendarPage
    from .panel_gaming import GamingPage
    from .panel_pages import AgentsPage, HabitsPage, MusicPage, NotesPage, TimerPage

    register("home", "Home", "home", HomePage)
    register("timer", "Timer", "clock", TimerPage)
    register("notes", "Notes", "notes", NotesPage)
    register("music", "Music", "note", MusicPage)
    register("agents", "Agents", "agent", AgentsPage)
    register("habits", "Habits", "habit", HabitsPage)
    register("calendar", "Calendar", "calendar", CalendarPage)
    register("gaming", "Gaming", "gamepad", GamingPage)
    try:
        from .panel_system import SystemPage

        register("system", "System", "sliders", SystemPage)
    except ImportError:
        pass
    try:
        from .panel_desk import ClipboardPage, DocsPage, LauncherPage, ShelfPage

        if ctrl.clipboard is not None:
            register("clipboard", "Clipboard", "clipboard", lambda c: ClipboardPage(c, c.clipboard))
        register("shelf", "Shelf", "shelf", ShelfPage)
        register("launcher", "Launcher", "grid", LauncherPage)
        register("docs", "Docs", "doc", DocsPage)
    except ImportError:
        pass
