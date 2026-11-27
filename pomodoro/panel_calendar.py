"""The panel's clock and weather card (Home), and the Calendar tab: your month, shaded by how much you focused."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QWidget

from . import notes
from .icons import draw_glyph
from .panel import Card, IconButton, Page, label, row
from .panel_pages import chip
from .stats import fmt_hm, plural
from .theme import FONT_FAMILY


def _font(px: float, weight=QFont.Normal) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(round(px))
    font.setWeight(weight)
    return font


def degrees(value: float | None) -> str:
    return "" if value is None else f"{round(value)}°"


# ------------------------------------------------------------------ the clock card (Home)
class WeatherGlyph(QWidget):
    def __init__(self, ctrl, size: int = 30):
        super().__init__()
        self.ctrl, self.glyph = ctrl, "cloud"
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        draw_glyph(p, self.glyph, QPointF(self.width() / 2, self.height() / 2), self.width() * 0.9,
                   QColor(self.ctrl.theme.ink_2))
        p.end()


class ClockCard(Card):
    """The time, the date, and today's weather for your city."""

    def __init__(self, ctrl, width: int | None = 220, open_calendar=None):
        super().__init__(ctrl, "", width)
        self.time = label("", "big")
        self.date = label("", "secondary")
        self.body.addStretch(1)
        self.body.addWidget(self.time)
        self.body.addWidget(self.date)
        self.icon = WeatherGlyph(ctrl, 28)
        self.temp = label("", "title")
        self.range = label("", "muted")
        self.place = label("", "muted")
        self.setup = chip("Add your city's weather", open_calendar or (lambda: None))
        self.body.addSpacing(6)
        self.body.addLayout(row(self.icon, self.temp, None))
        self.body.addWidget(self.range)
        self.body.addWidget(self.place)
        self.body.addWidget(self.setup)
        self.body.addStretch(1)
        ctrl.weather.changed.connect(self.refresh)
        self.refresh()

    def tick(self) -> None:
        now = datetime.now()
        self.time.setText(now.strftime("%I:%M %p").lstrip("0"))
        self.date.setText(f"{now:%A}, {now.day} {now:%B}")

    def refresh(self) -> None:
        self.tick()
        service = self.ctrl.weather
        place, w = service.place, service.now
        has = place is not None
        for widget in (self.icon, self.temp, self.range, self.place):
            widget.setVisible(has)
        self.setup.setVisible(not has)
        if has:
            service.refresh()
            self.place.setText(place.name.split(",")[0])
            if w is None:
                self.temp.setText("…")
                self.range.setText("Getting the weather")
            else:
                self.icon.glyph = w.glyph
                self.icon.update()
                self.temp.setText(f"{degrees(w.temp)} {w.words}")
                self.range.setText(f"H {degrees(w.high)}  L {degrees(w.low)}" if w.high is not None else "")


# ------------------------------------------------------------------ the month
class MonthView(QWidget):
    """A month of days, each tinted by how much you focused that day. Click one to see it."""

    picked = Signal(object)  # a date

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        today = date.today()
        self.year, self.month, self.selected = today.year, today.month, today
        self.minutes: dict[date, int] = {}
        self.setMinimumSize(QSize(300, 200))
        self.setMouseTracking(True)

    def set_month(self, year: int, month: int, minutes: dict[date, int]) -> None:
        self.year, self.month, self.minutes = year, month, minutes
        self.update()

    def _cells(self) -> list[tuple[date, QRectF]]:
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(self.year, self.month)
        top = 22.0
        w = self.width() / 7
        h = (self.height() - top) / max(1, len(weeks))
        out = []
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                out.append((day, QRectF(c * w, top + r * h, w, h)))
        return out

    def mousePressEvent(self, event) -> None:
        for day, rect in self._cells():
            if rect.contains(event.position()) and day.month == self.month:
                self.selected = day
                self.picked.emit(day)
                self.update()
                return

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(_font(11, QFont.DemiBold))
        p.setPen(QColor(t.muted))
        w = self.width() / 7
        for c, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            p.drawText(QRectF(c * w, 0, w, 18), Qt.AlignCenter, name)
        heat, best = t.heat, max(self.minutes.values() or [0])
        today = date.today()
        for day, rect in self._cells():
            inner = rect.adjusted(3, 2, -3, -2)
            mins = self.minutes.get(day, 0)
            outside = day.month != self.month
            text = QColor(t.muted if outside else t.ink)
            if mins and not outside:
                level = min(len(heat) - 1, int((len(heat) - 1) * mins / max(best, 1) + 0.5))
                cell = QColor(heat[level])
                p.setPen(Qt.NoPen)
                p.setBrush(cell)
                p.drawRoundedRect(inner, 8, 8)
                light, dark = QColor(t.surface if not t.dark else t.ink), QColor(t.ink if not t.dark else t.page)
                text = light if cell.lightness() < 135 else dark  # readable on any shade of the ramp
            if day == self.selected and not outside:
                p.setPen(QPen(QColor(t.ink), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(inner, 8, 8)
            elif day == today:
                p.setPen(QPen(QColor(t.chart), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(inner, 8, 8)
            p.setPen(text)
            p.setFont(_font(12, QFont.DemiBold if day == today else QFont.Normal))
            p.drawText(inner, Qt.AlignCenter, str(day.day))
        p.end()


class CalendarPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        today = date.today()
        self.year, self.month = today.year, today.month
        month = Card(ctrl, "", 380)
        self.title = label("", "title")
        prev, nxt = IconButton(ctrl, "previous", "Previous month", 28), IconButton(ctrl, "skip", "Next month", 28)
        prev.clicked.connect(lambda: self._shift(-1))
        nxt.clicked.connect(lambda: self._shift(1))
        month.body.addLayout(row(self.title, None, prev, nxt))
        self.view = MonthView(ctrl)
        self.view.picked.connect(self._pick)
        month.body.addWidget(self.view, 1)
        self.add(month)

        day = Card(ctrl, "That day")
        day.action("notes", "Open that day's note", self._open_note)
        self.day_title = label("", "title")
        self.day_info = label("", "secondary")
        self.day_list = QListWidget()
        day.body.addWidget(self.day_title)
        day.body.addWidget(self.day_info)
        day.body.addWidget(self.day_list, 1)
        self.add(day, 1)

        weather = Card(ctrl, "Weather", 300)
        weather.action("refresh", "Refresh the weather", lambda: ctrl.weather.refresh(force=True))
        self.weather_now = label("", "title", wrap=True)
        self.weather_place = label("", "muted", wrap=True)
        self.city = QLineEdit()
        self.city.setObjectName("jot")
        self.city.setPlaceholderText("Your city, then Enter")
        self.city.returnPressed.connect(lambda: ctrl.weather.search(self.city.text()))
        self.results = QListWidget()
        self.results.itemClicked.connect(self._choose_place)
        self.forget = chip("No weather", lambda: ctrl.weather.set_place(None))
        weather.body.addWidget(self.weather_now)
        weather.body.addWidget(self.weather_place)
        weather.body.addWidget(self.city)
        weather.body.addWidget(self.results, 1)
        weather.body.addWidget(label("From Open-Meteo: only your city's name and location are sent.", "muted",
                                     wrap=True))
        weather.body.addLayout(row(self.forget, None))
        self.add(weather)
        ctrl.weather.changed.connect(self._show_weather)
        ctrl.weather.found.connect(self._show_places)

    # ------------------------------------------------------------- the month
    def _shift(self, step: int) -> None:
        month = self.month + step
        self.year, self.month = self.year + (month - 1) // 12, (month - 1) % 12 + 1
        self.refresh()

    def _month_minutes(self) -> dict[date, int]:
        first = date(self.year, self.month, 1)
        out: dict[date, int] = {}
        for s in self.ctrl.storage.sessions(since=first):
            if s.mode == "focus" and s.day.month == self.month and s.day.year == self.year:
                out[s.day] = out.get(s.day, 0) + s.actual_sec // 60
        return out

    def refresh(self) -> None:
        minutes = self._month_minutes()
        self.title.setText(f"{calendar.month_name[self.month]} {self.year}")
        total = sum(minutes.values())
        self.view.set_month(self.year, self.month, minutes)
        if self.view.selected.month != self.month:
            self.view.selected = date(self.year, self.month, 1)
        self._pick(self.view.selected)
        self.title.setToolTip(f"{fmt_hm(total * 60)} of focus this month" if total else "")
        self._show_weather()

    def _pick(self, day: date) -> None:
        sessions = [s for s in self.ctrl.storage.sessions(since=day) if s.day == day and s.mode == "focus"]
        self.day_title.setText(f"{day:%A}, {day.day} {day:%B}")
        focus = sum(s.actual_sec for s in sessions)
        self.day_info.setText(f"{plural(len(sessions), 'focus session')}, {fmt_hm(focus)}" if sessions
                              else "No focus sessions")
        self.day_list.clear()
        for s in sessions:
            what = f" on {s.task}" if s.task else ""
            self.day_list.addItem(f"{s.started_at:%H:%M}  ·  {max(1, round(s.actual_sec / 60))} min{what}")
        from .stickies import clock
        for note in self.ctrl.stickies.notes:
            when = note.reminder()
            if when is not None and when.date() == day:
                item = QListWidgetItem(f"{clock(when)}  ·  Reminder: {note.first_line() or 'sticky note'}")
                self.day_list.addItem(item)

    def _open_note(self) -> None:
        day = self.view.selected
        if day == date.today():
            self.ctrl.open_daily_note()
            return
        vault = self.ctrl.notes_vault()
        path = notes.daily_path(vault, day)
        if not path.exists():
            vault.write(path, f"# {day:%A}, {day.day} {day:%B} {day.year}\n\n")
        self.ctrl.open_notes(path)

    # ------------------------------------------------------------- weather
    def _show_weather(self) -> None:
        service = self.ctrl.weather
        place, w = service.place, service.now
        self.forget.setVisible(place is not None)
        if place is None:
            self.weather_now.setText("No city yet")
            self.weather_place.setText("Type your city to see its weather on Home.")
            return
        self.weather_place.setText(place.name)
        if w is None:
            self.weather_now.setText("Getting the weather…")
            service.refresh()
        else:
            high = f", high {degrees(w.high)}, low {degrees(w.low)}" if w.high is not None else ""
            self.weather_now.setText(f"{degrees(w.temp)} {w.words}{high}")

    def _show_places(self, places: list) -> None:
        self.results.clear()
        for place in places:
            item = QListWidgetItem(place.name)
            item.setData(Qt.UserRole, place)
            self.results.addItem(item)
        if not places:
            empty = QListWidgetItem("No place by that name (or no internet)")
            empty.setFlags(Qt.NoItemFlags)
            self.results.addItem(empty)

    def _choose_place(self, item: QListWidgetItem) -> None:
        place = item.data(Qt.UserRole)
        if place is not None:
            self.ctrl.weather.set_place(place)
            self.results.clear()
            self.city.clear()
