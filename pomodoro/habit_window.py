"""The habit tracker window: your rank, the ladder of medals, and the month as a grid of habits and days.

The grid works like a habit tracker spreadsheet: a row per habit with its monthly goal, a box per day,
and each habit's progress toward its goal; under it, the habits done each day and the points each day
earned. Today and yesterday can be ticked; older days are final.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import habits, medals
from .charts import ui_font
from .stats import plural
from .winutil import set_dark_title_bar

WEEKDAYS = "MTWTFSS"


def _label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


class MedalView(QWidget):
    """One big medal."""

    def __init__(self, radius: float, parent: QWidget | None = None):
        super().__init__(parent)
        self.radius = radius
        self.rank: habits.Rank | None = None
        self.setFixedSize(QSize(round(radius * 3.3), round(radius * 2.8)))

    def set_rank(self, rank: habits.Rank | None) -> None:
        self.rank = rank
        self.update()

    def paintEvent(self, _event) -> None:
        if self.rank is None:
            return
        p = QPainter(self)
        _side, top, bottom = medals.extent(self.rank.medal)
        cy = (self.height() - (top + bottom) * self.radius) / 2 + top * self.radius
        medals.paint_medal(p, QPointF(self.width() / 2, cy), self.radius, self.rank)
        p.end()


class RankBar(QWidget):
    """Progress toward your next star, with today's open match."""

    def __init__(self, ctrl, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.standing: habits.Standing | None = None
        self.setFixedHeight(10)
        self.setMinimumWidth(220)

    def set_standing(self, standing) -> None:
        self.standing = standing
        self.update()

    def paintEvent(self, _event) -> None:
        if self.standing is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.ctrl.theme
        medals.paint_bar(p, QRectF(self.rect()), self.standing, t.dark, QColor(t.danger))
        p.end()


class LadderView(QWidget):
    """Every medal from Rookie to Eternal, with yours lit up and the ones above still ahead."""

    def __init__(self, ctrl, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.medal = -1
        self.setFixedHeight(70)
        self.setMinimumWidth(len(habits.MEDALS) * 70)

    def set_medal(self, medal: int) -> None:
        self.medal = medal
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        t = self.ctrl.theme
        n = len(habits.MEDALS)
        step = self.width() / n
        for i, name in enumerate(habits.MEDALS):
            cx = step * (i + 0.5)
            mine = i == self.medal
            if mine:
                band = QColor(t.hover)
                p.setPen(Qt.NoPen)
                p.setBrush(band)
                p.drawRoundedRect(QRectF(cx - step / 2 + 3, 0, step - 6, self.height()), 10, 10)
            medals.paint_medal(p, QPointF(cx, 26), 13, habits.Rank(i, 0, 0, 0, 0), faded=i > self.medal)
            p.setFont(ui_font(11, QFont.DemiBold if mine else QFont.Normal))
            p.setPen(QColor(t.ink if mine else t.muted))
            p.drawText(QRectF(cx - step / 2, 48, step, 18), Qt.AlignCenter, name)
        p.end()


class HabitGrid(QWidget):
    """The month: a row per habit, a box per day, progress toward each monthly goal, and the day totals."""

    PAD, NAME_W, GOAL_W, CELL, PROG_W = 12.0, 168.0, 46.0, 23.0, 112.0
    HEAD_H, ROW_H, FOOT_H = 42.0, 27.0, 23.0

    def __init__(self, ctrl, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.view: habits.Month | None = None
        self.ticks: dict[date, set[str]] = {}
        self.today = date.today()
        self.deltas: dict[date, int] = {}
        self.pending: int | None = None
        self.hover: tuple[int, int] | None = None
        self.setMouseTracking(True)

    def set_month(self, view: habits.Month, ticks: dict, today: date, deltas: dict, pending: int | None) -> None:
        self.view, self.ticks, self.today, self.deltas, self.pending = view, ticks, today, deltas, pending
        self.hover = None
        w = 2 * self.PAD + self.NAME_W + self.GOAL_W + self.CELL * view.days + self.PROG_W
        h = 2 * self.PAD + self.HEAD_H + self.ROW_H * len(view.rows) + 2 * self.FOOT_H
        self.setFixedSize(round(w), round(h))
        self.update()

    # ------------------------------------------------------------- geometry
    def _day(self, i: int) -> date:
        return date(self.view.year, self.view.month, 1) + timedelta(days=i)

    def _x(self, i: int) -> float:
        return self.PAD + self.NAME_W + self.GOAL_W + i * self.CELL

    def _y(self, row: int) -> float:
        return self.PAD + self.HEAD_H + row * self.ROW_H

    def _box(self, row: int, i: int) -> QRectF:
        s = 17.0
        return QRectF(self._x(i) + (self.CELL - s) / 2, self._y(row) + (self.ROW_H - s) / 2, s, s)

    def _cell_at(self, pos: QPointF) -> tuple[int, int] | None:
        if self.view is None:
            return None
        i = int((pos.x() - self._x(0)) // self.CELL)
        row = int((pos.y() - self._y(0)) // self.ROW_H)
        if 0 <= i < self.view.days and 0 <= row < len(self.view.rows):
            return row, i
        return None

    def _editable(self, row: int, i: int) -> bool:
        habit = self.view.rows[row][0]
        day = self._day(i)
        return habits.editable(day, self.today) and habits.is_active(habit, day)

    # ------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        if self.view is None:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        t, v = self.ctrl.theme, self.view
        standing = getattr(self.ctrl, "habit_standing", None)
        medal = standing.rank.medal if standing is not None else 0
        fill = medals.bar_color(medal, t.dark)
        p.setPen(QPen(t.border, 1))
        p.setBrush(QColor(t.surface))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        bottom = self._y(len(v.rows)) + 2 * self.FOOT_H
        for i in range(v.days):  # today's column, and yesterday's (still open), stand out
            day = self._day(i)
            if day in (self.today, self.today - timedelta(days=1)):
                band = QColor(t.hover)
                if day != self.today:
                    band.setAlpha(110)
                p.setPen(Qt.NoPen)
                p.setBrush(band)
                p.drawRoundedRect(QRectF(self._x(i) + 1, self.PAD - 4, self.CELL - 2, bottom - self.PAD + 8), 7, 7)

        head = ui_font(10.5, QFont.DemiBold)
        p.setFont(head)
        p.setPen(QColor(t.muted))
        y0 = self.PAD
        p.drawText(QRectF(self.PAD + 6, y0, self.NAME_W, 22), Qt.AlignLeft | Qt.AlignVCenter, "HABIT")
        p.drawText(QRectF(self.PAD + self.NAME_W, y0, self.GOAL_W, 22), Qt.AlignCenter, "GOAL")
        p.drawText(QRectF(self._x(v.days) + 8, y0, self.PROG_W - 8, 22), Qt.AlignLeft | Qt.AlignVCenter, "PROGRESS")
        for i in range(v.days):
            day = self._day(i)
            today = day == self.today
            p.setFont(ui_font(11, QFont.Bold if today else QFont.DemiBold))
            p.setPen(QColor(t.ink if today else t.ink_2))
            p.drawText(QRectF(self._x(i), y0, self.CELL, 22), Qt.AlignCenter, str(i + 1))
            p.setFont(ui_font(10))
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(self._x(i), y0 + 20, self.CELL, 18), Qt.AlignCenter, WEEKDAYS[day.weekday()])

        grid = QColor(t.grid)
        name_font, small = ui_font(12.5), ui_font(11)
        for row, (habit, times, goal) in enumerate(v.rows):
            y = self._y(row)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(self.PAD, y), QPointF(self.width() - self.PAD, y))
            retired = bool(habit["until"]) and habit["until"] <= self.today.isoformat()
            p.setFont(name_font)
            p.setPen(QColor(t.muted if retired else t.ink))
            name = QFontMetricsF(name_font).elidedText(habit["name"], Qt.ElideRight, self.NAME_W - 12)
            p.drawText(QRectF(self.PAD + 6, y, self.NAME_W - 6, self.ROW_H), Qt.AlignLeft | Qt.AlignVCenter, name)
            p.setFont(small)
            p.setPen(QColor(t.ink_2))
            p.drawText(QRectF(self.PAD + self.NAME_W, y, self.GOAL_W, self.ROW_H), Qt.AlignCenter, str(goal))
            for i in range(v.days):
                self._paint_box(p, row, i, habit, fill)
            # progress toward the monthly goal
            x = self._x(v.days) + 8
            met = times >= goal
            p.setFont(ui_font(11, QFont.DemiBold if met else QFont.Normal))
            p.setPen(QColor(t.success if met else t.ink_2))
            p.drawText(QRectF(x, y, 44, self.ROW_H), Qt.AlignLeft | Qt.AlignVCenter, f"{times}/{goal}")
            bar = QRectF(x + 46, y + self.ROW_H / 2 - 3, self.PROG_W - 62, 6)
            track = QColor(fill)
            track.setAlpha(55)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(bar, 3, 3)
            share = min(1.0, times / goal) if goal else 0.0
            if share > 0:
                p.setBrush(fill)
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * share), bar.height()), 3, 3)

        # the totals: habits done each day, and the points each day earned
        y = self._y(len(v.rows))
        p.setPen(QPen(grid, 1))
        p.drawLine(QPointF(self.PAD, y), QPointF(self.width() - self.PAD, y))
        p.setFont(ui_font(11, QFont.DemiBold))
        p.setPen(QColor(t.ink_2))
        p.drawText(QRectF(self.PAD + 6, y, self.NAME_W, self.FOOT_H), Qt.AlignLeft | Qt.AlignVCenter, "Done")
        p.drawText(QRectF(self.PAD + 6, y + self.FOOT_H, self.NAME_W, self.FOOT_H), Qt.AlignLeft | Qt.AlignVCenter,
                   "Points")
        p.setFont(ui_font(9.5, QFont.DemiBold))
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(self._x(v.days) + 8, y, self.PROG_W - 8, self.FOOT_H), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{v.done}/{v.goal} this month")
        for i in range(v.days):
            day = self._day(i)
            if day > self.today:
                continue
            p.setFont(ui_font(11))
            p.setPen(QColor(t.ink_2 if v.per_day[i] else t.muted))
            p.drawText(QRectF(self._x(i), y, self.CELL, self.FOOT_H), Qt.AlignCenter, str(v.per_day[i]))
            delta, live = self.deltas.get(day), False
            if day == self.today and self.pending is not None:
                delta, live = self.pending, True
            if delta is None:
                continue
            ink = QColor(t.success if delta == habits.WIN else t.ink_2 if delta > 0 else t.danger)
            if live:
                ink.setAlpha(150)
            font = ui_font(9.5, QFont.DemiBold)
            font.setItalic(live)
            p.setFont(font)
            p.setPen(ink)
            p.drawText(QRectF(self._x(i) - 2, y + self.FOOT_H, self.CELL + 4, self.FOOT_H), Qt.AlignCenter,
                       habits.signed(delta))
        p.end()

    def _paint_box(self, p: QPainter, row: int, i: int, habit: dict, fill: QColor) -> None:
        t = self.ctrl.theme
        day = self._day(i)
        box = self._box(row, i)
        if not habits.is_active(habit, day):
            return
        done = habit["id"] in self.ticks.get(day, ())
        editable = habits.editable(day, self.today)
        hot = self.hover == (row, i) and editable
        if done:
            color = QColor(fill).lighter(108) if hot else QColor(fill)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(box, 5, 5)
            check = QPainterPath(QPointF(box.left() + 4.5, box.center().y() + 0.5))
            check.lineTo(QPointF(box.left() + 7.8, box.bottom() - 4.8))
            check.lineTo(QPointF(box.right() - 4.2, box.top() + 5))
            p.setPen(QPen(QColor("#ffffff" if not t.dark else t.on_accent), 2.0, Qt.SolidLine, Qt.RoundCap,
                          Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(check)
            return
        if day > self.today:
            edge = QColor(t.grid)
            p.setPen(QPen(edge, 1))
            p.setBrush(Qt.NoBrush)
        elif editable:
            p.setPen(QPen(QColor(fill if hot else t.axis), 1.4))
            p.setBrush(QColor(t.hover) if hot else Qt.NoBrush)
        else:  # final, and not done
            edge = QColor(t.axis)
            edge.setAlpha(120)
            p.setPen(QPen(edge, 1))
            p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)

    # ------------------------------------------------------------- input
    def mouseMoveEvent(self, event) -> None:
        cell = self._cell_at(event.position())
        if cell != self.hover:
            self.hover = cell
            self.setCursor(Qt.PointingHandCursor if cell and self._editable(*cell) else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:
        self.hover = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        cell = self._cell_at(event.position())
        if cell is not None and self._editable(*cell):
            row, i = cell
            self.ctrl.toggle_habit(self.view.rows[row][0]["id"], self._day(i))

    def event(self, e) -> bool:
        if e.type() == QEvent.ToolTip and self.view is not None:
            text = self._tip(QPointF(e.pos()))
            if text:
                QToolTip.showText(e.globalPos(), text, self)
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    def _tip(self, pos: QPointF) -> str:
        cell = self._cell_at(pos)
        if cell is not None:
            row, i = cell
            habit = self.view.rows[row][0]
            day = self._day(i)
            when = f"{habit['name']}  ·  {day:%A} {day.day} {day:%B}"
            if not habits.is_active(habit, day):
                return f"{when}\n" + ("Not tracked yet" if day.isoformat() < habit["since"] else "No longer tracked")
            done = habit["id"] in self.ticks.get(day, ())
            if day > self.today:
                return f"{when}\nComing up"
            if habits.editable(day, self.today):
                return f"{when}\n{'Done' if done else 'Not done'}: click to {'untick' if done else 'tick'} it"
            return f"{when}\n{'Done' if done else 'Not done'} (final)"
        y = self._y(len(self.view.rows)) + self.FOOT_H
        i = int((pos.x() - self._x(0)) // self.CELL)
        if y <= pos.y() <= y + self.FOOT_H and 0 <= i < self.view.days:
            day = self._day(i)
            if day == self.today and self.pending is not None:
                return "Today is still open: it counts once the day is over"
            delta = self.deltas.get(day)
            if delta is not None:
                return {habits.WIN: "Daily target reached", habits.HALF: "At least half the target"}.get(
                    delta, "Less than half the target") + f": {habits.signed(delta)} points"
        return ""


class HabitEditor(QDialog):
    """Your habits, each with a monthly goal, and the daily target."""

    def __init__(self, ctrl, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.today = date.today()
        self.setWindowTitle("Pomo · Your habits")
        self.setMinimumWidth(520)
        self.rows: list[tuple[str | None, QLineEdit, QSpinBox, QWidget]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(_label("Your habits", "h1"))
        lay.addWidget(_label("Give each habit a monthly goal: on how many days of the month you want to do it. "
                             "31 means every day.", "secondary", wrap=True))
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(6)
        self.list.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(260)
        lay.addWidget(scroll, 1)
        add = QPushButton("Add a habit")
        add.setObjectName("ghost")
        add.clicked.connect(lambda: self._add_row(None, "", habits.GOAL_MAX, focus=True))
        lay.addWidget(add, 0, Qt.AlignLeft)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Daily target"))
        self.target = QSpinBox()
        self.target.setRange(0, habits.MAX_HABITS)
        self.target.setSuffix(" habits")
        self.target.setSpecialValueText("Auto")
        self.target.setValue(ctrl.settings.habit_target)
        self.target.valueChanged.connect(self._update_target)
        target_row.addWidget(self.target)
        self.target_note = _label("", "muted")
        target_row.addWidget(self.target_note, 1)
        lay.addLayout(target_row)
        lay.addWidget(_label(f"Every day is a match: reach the target for +{habits.WIN} points, at least half of it "
                             f"for +{habits.HALF}, less than half costs {-habits.LOSS}.", "muted", wrap=True))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        for h in habits.current(ctrl.settings.habits, self.today):
            self._add_row(h["id"], h["name"], h["goal"])
        if not self.rows:
            self._add_row(None, "", habits.GOAL_MAX, focus=True)
        self._update_target()

    def _add_row(self, hid: str | None, name: str, goal: int, focus: bool = False) -> None:
        if len(self.rows) >= habits.MAX_HABITS:
            return
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(name)
        edit.setPlaceholderText("A habit, like Read 10 pages")
        edit.setMaxLength(habits.NAME_MAX)
        spin = QSpinBox()
        spin.setRange(1, habits.GOAL_MAX)
        spin.setSuffix(" days a month")
        spin.setValue(goal)
        spin.valueChanged.connect(self._update_target)
        remove = QPushButton("✕")
        remove.setObjectName("ghost")
        remove.setToolTip("Stop tracking this habit (the days it already counted stay as they are)")
        remove.setFixedWidth(34)
        entry = (hid, edit, spin, row)
        remove.clicked.connect(lambda: self._remove(entry))
        lay.addWidget(edit, 1)
        lay.addWidget(spin)
        lay.addWidget(remove)
        self.list.insertWidget(self.list.count() - 1, row)
        self.rows.append(entry)
        if focus:
            edit.setFocus()
        self._update_target()

    def _remove(self, entry) -> None:
        if entry in self.rows:
            self.rows.remove(entry)
            entry[3].deleteLater()
            self._update_target()

    def rows_out(self) -> list[tuple[str | None, str, int]]:
        return [(hid, edit.text(), spin.value()) for hid, edit, spin, _row in self.rows]

    def _update_target(self) -> None:
        if not hasattr(self, "target_note"):
            return
        goals = [spin.value() for _hid, edit, spin, _row in self.rows if edit.text().strip()] or \
            [spin.value() for *_x, spin, _row in self.rows]
        n = habits.days_in_month(self.today.year, self.today.month)
        auto = max(1, min(len(goals), sum(min(g, n) for g in goals) // n)) if goals else 0
        self.target.setSpecialValueText(f"Auto ({auto})" if auto else "Auto")
        if self.target.value():
            self.target_note.setText("habits a day, whatever your monthly goals say")
        else:
            self.target_note.setText("what your monthly goals add up to per day")


class HabitWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.setWindowTitle("Pomo · Habits")
        self.setWindowIcon(ctrl.icon)
        self.resize(1150, 880)
        today = date.today()
        self.year, self.month = today.year, today.month
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(16)
        self.medal = MedalView(36)
        header.addWidget(self.medal)
        info = QVBoxLayout()
        info.setSpacing(4)
        self.rank_label = _label("", "h1")
        self.points_label = _label("", "secondary")
        self.bar = RankBar(ctrl)
        self.today_label = _label("", "secondary", wrap=True)
        self.last_label = _label("", "muted", wrap=True)
        for w in (self.rank_label, self.points_label, self.bar, self.today_label, self.last_label):
            info.addWidget(w)
        info.addStretch(1)
        header.addLayout(info, 1)
        actions = QVBoxLayout()
        self.edit_btn = QPushButton("Edit habits…")
        self.edit_btn.setObjectName("primary")
        self.edit_btn.clicked.connect(self.edit)
        self.import_btn = QPushButton("Import from Excel…")
        self.import_btn.setObjectName("ghost")
        self.import_btn.clicked.connect(self._import)
        actions.addWidget(self.edit_btn)
        actions.addWidget(self.import_btn)
        actions.addStretch(1)
        header.addLayout(actions)
        lay.addLayout(header)

        self.empty = _label("", "secondary", wrap=True)
        self.empty.setTextFormat(Qt.RichText)
        self.empty.setText(
            "<b>No habits yet.</b><br><br>List the habits you want to keep, each with a monthly goal (on how "
            "many days of the month you want to do it), and tick them off each day. Keeping them earns you a "
            f"rank, like a game's ranked medals: a day on target is +{habits.WIN} points, half of it "
            f"+{habits.HALF}, and less than half costs {-habits.LOSS}. Climb from Rookie to Eternal, and "
            "don't slide back.<br><br>Already track your habits in Excel? Import them.")
        lay.addWidget(self.empty)
        self.ladder = LadderView(ctrl)
        lay.addWidget(self.ladder)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("‹")
        self.next_btn = QPushButton("›")
        for b, step in ((self.prev_btn, -1), (self.next_btn, 1)):
            b.setObjectName("ghost")
            b.setFixedWidth(34)
            b.clicked.connect(lambda _c=False, s=step: self._shift(s))
        self.month_label = _label("", "title")
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.month_label)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        self.rate_label = _label("", "secondary")
        nav.addWidget(self.rate_label)
        lay.addLayout(nav)

        self.grid = HabitGrid(ctrl)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.grid)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(self.scroll, 1)
        self.hint = _label(f"Tick each habit as you do it. Yesterday stays open for what you forgot; older days "
                           f"are final. Every day is a match: the daily target earns +{habits.WIN} points, at "
                           f"least half of it +{habits.HALF}, less costs {-habits.LOSS}. {habits.STAR_POINTS} "
                           f"points earn a star, and {habits.STARS} stars the next medal.", "muted", wrap=True)
        lay.addWidget(self.hint)
        self.filler = QWidget()  # takes the room the grid would, so the empty state stays at the top
        lay.addWidget(self.filler, 1)
        self.refresh()

    # ------------------------------------------------------------- showing
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.refresh()

    def refresh(self) -> None:
        s, today = self.ctrl.settings, date.today()
        standing = self.ctrl.habit_standing
        tracked = bool(s.habits)
        for w in (self.prev_btn, self.next_btn, self.month_label, self.rate_label, self.scroll, self.hint):
            w.setVisible(tracked)
        self.empty.setVisible(not tracked)
        self.filler.setVisible(not tracked)
        self.ladder.set_medal(standing.rank.medal if standing is not None else -1)  # none yet: all still ahead
        for w in (self.medal, self.bar, self.points_label, self.today_label, self.last_label):
            w.setVisible(standing is not None)
        self.edit_btn.setText("Edit habits…" if tracked else "Add habits…")
        if standing is None:
            self.rank_label.setText("Habits" if not tracked else "Habits: none tracked today")
        else:
            rank = standing.rank
            self.medal.set_rank(rank)
            self.rank_label.setText(rank.label)
            self.points_label.setText(f"{standing.points:,} points  ·  {rank.to_next} to {rank.next_label}")
            self.bar.set_standing(standing)
            self.today_label.setText(habits.today_line(standing))
            self.last_label.setText(self._last_text(standing, today))
        if not tracked:
            return
        self.year, self.month = self._clamp_month(self.year, self.month)
        view = habits.month_view(s.habits, self.ctrl.habit_ticks, self.year, self.month)
        self.month_label.setText(date(self.year, self.month, 1).strftime("%B %Y"))
        self.rate_label.setText(f"Success rate {view.rate:.0%}  ·  {view.verdict}  ·  "
                                f"{view.done} of {view.goal} for your monthly goals")
        first = self._first_month()
        self.prev_btn.setEnabled((self.year, self.month) > first)
        self.next_btn.setEnabled((self.year, self.month) < (today.year, today.month))
        deltas = {d.day: d.delta for d in standing.days} if standing is not None else {}
        self.grid.set_month(view, self.ctrl.habit_ticks, today, deltas,
                            standing.pending if standing is not None else None)

    @staticmethod
    def _last_text(standing, today: date) -> str:
        last = standing.last
        if last is None:
            return "Your first match is today."
        yesterday = today - timedelta(days=1)
        when = "Yesterday" if last.day == yesterday else f"{last.day:%A} {last.day.day} {last.day:%B}"
        text = f"{when}: {last.done} of {plural(last.target, 'habit')}, {habits.signed(last.delta)} points."
        if last.day == yesterday and last.delta < habits.WIN:
            text += " Forgot to tick something? Yesterday can still be ticked."
        return text

    def _first_month(self) -> tuple[int, int]:
        start = habits.start_day(self.ctrl.settings.habits) or date.today()
        return start.year, start.month

    def _clamp_month(self, year: int, month: int) -> tuple[int, int]:
        today = date.today()
        return min(max((year, month), self._first_month()), (today.year, today.month))

    def _shift(self, step: int) -> None:
        month = self.month + step
        year = self.year + (month - 1) // 12
        self.year, self.month = year, (month - 1) % 12 + 1
        self.refresh()

    # ------------------------------------------------------------- changes
    def edit(self) -> None:
        editor = HabitEditor(self.ctrl, self)
        if editor.exec() == QDialog.Accepted:
            self.ctrl.save_habits(editor.rows_out(), editor.target.value())

    def _import(self) -> None:
        start = Path.home() / "Downloads"
        path, _filter = QFileDialog.getOpenFileName(self, "Import habits from Excel",
                                                    str(start if start.is_dir() else Path.home()),
                                                    "Excel workbooks (*.xlsx)")
        if path:
            self.import_file(Path(path))

    def import_file(self, path: Path) -> None:
        try:
            found = habits.read_tracker(path)
        except ValueError as exc:
            QMessageBox.information(self, "Import habits", str(exc))
            return
        names = "\n".join(f"•  {name}  ({goal} days a month)" for name, goal in found)
        answer = QMessageBox.question(
            self, "Import habits",
            f"Pomo found {plural(len(found), 'habit')} in {path.name}:\n\n{names}\n\nAdd them? A habit you already "
            "track gets the goal from the file. Your rank counts them from today.")
        if answer != QMessageBox.Yes:
            return
        added, updated = self.ctrl.import_habits(found)
        parts = [plural(added, "habit") + " added"] + ([f"{updated} goal{'s' if updated != 1 else ''} updated"]
                                                         if updated else [])
        QMessageBox.information(self, "Import habits", " and ".join(parts) + ". Tick today's habits as you do them!")
