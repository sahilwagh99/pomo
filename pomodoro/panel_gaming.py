"""The panel's Gaming tab: live FPS, GPU, CPU and memory, with the last minute drawn under each.

It reads live whenever it's open (a handy performance monitor), and is where the panel opens in gaming mode.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QMessageBox, QWidget

from .gaming import fmt_pct, fmt_temp, game_name, temp_hot
from .panel import Card, Page, label, row
from .panel_pages import chip
from .stats import fmt_hm
from .theme import mode_color


class Spark(QWidget):
    """The last minute of one reading, as a soft line."""

    def __init__(self, ctrl, key: str, top: float = 100.0):
        super().__init__()
        self.ctrl, self.key, self.top = ctrl, key, top
        self.setMinimumHeight(38)

    def paintEvent(self, _event) -> None:
        values = list(self.ctrl.gaming.history.get(self.key, ()))
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 3, -1, -2)
        p.setPen(QPen(QColor(t.grid), 1))
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        if len(values) >= 2:
            top = max(self.top, max(values) * 1.1)
            step = rect.width() / max(1, 59)
            x0 = rect.right() - step * (len(values) - 1)
            points = [QPointF(x0 + i * step, rect.bottom() - rect.height() * min(1.0, v / top))
                      for i, v in enumerate(values)]
            line = QPainterPath(points[0])
            for pt in points[1:]:
                line.lineTo(pt)
            fill = QPainterPath(line)
            fill.lineTo(points[-1].x(), rect.bottom())
            fill.lineTo(points[0].x(), rect.bottom())
            fill.closeSubpath()
            color = mode_color(t, self.ctrl.settings, "focus")
            wash = QColor(color)
            wash.setAlpha(60)
            p.fillPath(fill, wash)
            p.setPen(QPen(QColor(t.chart), 1.6))
            p.drawPath(line)
        p.end()


class StatCard(Card):
    def __init__(self, ctrl, title: str, key: str, width: int, top: float = 100.0):
        super().__init__(ctrl, title, width)
        self.big = label("", "big")
        self.detail = label("", "secondary", wrap=True)
        self.spark = Spark(ctrl, key, top)
        self.body.addWidget(self.big)
        self.body.addWidget(self.detail)
        self.body.addStretch(1)
        self.body.addWidget(self.spark)

    def show_values(self, big: str, detail: str, hot: bool = False) -> None:
        self.big.setText(big or "n/a")
        t = self.ctrl.theme
        self.detail.setStyleSheet(f"color: {t.danger if hot else t.ink_2};")
        self.detail.setText(detail)
        self.spark.update()


class GamingPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.fps = self.add(StatCard(ctrl, "FPS", "fps", 220, top=144))
        self.fps_setup = chip("Turn on FPS", self._fps_setup)
        self.fps.body.insertWidget(2, self.fps_setup)
        self.gpu = self.add(StatCard(ctrl, "GPU", "gpu", 210))
        self.cpu = self.add(StatCard(ctrl, "CPU", "cpu", 210))
        self.ram = self.add(StatCard(ctrl, "Memory", "ram", 190))
        game = Card(ctrl, "Game")
        self.game_name = label("", "title", wrap=True)
        self.game_info = label("", "secondary", wrap=True)
        self.toggle = chip("Gaming mode now", self._toggle)
        self.not_game = chip("Not a game", self._not_game)
        self.auto = QCheckBox("Switch by itself when I play")
        self.auto.toggled.connect(self._set_auto)
        game.body.addWidget(self.game_name)
        game.body.addWidget(self.game_info)
        game.body.addStretch(1)
        game.body.addLayout(row(self.toggle, self.not_game, None))
        game.body.addWidget(self.auto)
        self.add(game, 1)
        ctrl.gaming.updated.connect(self._update)
        ctrl.gaming.changed.connect(lambda _on: self.refresh())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.ctrl.gaming.watch(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.ctrl.gaming.watch(False)

    def refresh(self) -> None:
        g = self.ctrl.gaming
        self.auto.blockSignals(True)
        self.auto.setChecked(self.ctrl.settings.gaming_on)
        self.auto.blockSignals(False)
        if g.active:
            self.game_name.setText(game_name(g.game) or "Gaming mode")
            since = time.monotonic() - g.since if g.since else 0
            self.game_info.setText(f"Playing for {fmt_hm(since)}. No timers until you're done."
                                   if since >= 60 else "No timers until you're done.")
            self.toggle.setText("Leave gaming mode")
        else:
            self.game_name.setText("Not gaming")
            self.game_info.setText("When a game is in front for a few seconds, Pomo switches to these readings.")
            self.toggle.setText("Gaming mode now")
        self.not_game.setVisible(g.active and g.game is not None)
        self._update(g.reading)

    def tick(self) -> None:
        if self.ctrl.gaming.active and self.ctrl.gaming.since:
            self.refresh()

    def _update(self, reading) -> None:
        if not self.isVisible():
            return
        if reading is None:
            for card in (self.fps, self.gpu, self.cpu, self.ram):
                card.show_values("…", "Reading")
            self.fps_setup.hide()
            return
        if reading.fps is not None:
            source = {"rtss": "from RivaTuner", "presentmon": "from PresentMon"}.get(reading.fps_source, "")
            self.fps.show_values(str(round(reading.fps)), source)
            self.fps_setup.hide()
        else:
            self.fps.show_values("n/a", self._fps_hint())
            self.fps_setup.setVisible(self._can_set_up_fps())
        self.gpu.show_values(fmt_pct(reading.gpu), "  ".join(b for b in (
            fmt_temp(reading.gpu_temp),
            f"{reading.vram_used_gb:.1f} GB VRAM" if reading.vram_used_gb is not None else "") if b),
            temp_hot(reading.gpu_temp))
        self.cpu.show_values(fmt_pct(reading.cpu), fmt_temp(reading.cpu_temp) or "Temperature: not shared by "
                             "Windows here", temp_hot(reading.cpu_temp))
        used = (f"{reading.ram_used_gb:.1f} of {reading.ram_total_gb:.0f} GB"
                if reading.ram_used_gb is not None and reading.ram_total_gb else "")
        self.ram.show_values(fmt_pct(reading.ram), used)

    # ------------------------------------------------------------- FPS
    @staticmethod
    def _can_set_up_fps() -> bool:
        try:
            from .telemetry import fps_permission, presentmon_path
        except ImportError:
            return False
        return presentmon_path() is not None and not fps_permission()

    def _fps_hint(self) -> str:
        if not self.ctrl.gaming.active:
            return "Shows while you play"
        return "Turn it on below, or run RivaTuner" if self._can_set_up_fps() else "Run RivaTuner to see it"

    def _fps_setup(self) -> None:
        from .telemetry import request_fps_permission

        box = QMessageBox(QMessageBox.Information, "Turn on FPS",
                          "To count a game's frames, Windows needs to let Pomo read its graphics events.",
                          QMessageBox.Ok | QMessageBox.Cancel, self.window())
        box.setInformativeText("Windows asks for an administrator's OK once, to add you to its Performance Log "
                               "Users group. Then sign out and back in, and FPS shows up in gaming mode. "
                               "(With RivaTuner or MSI Afterburner running, Pomo shows FPS without this.)")
        box.button(QMessageBox.Ok).setText("Turn on FPS")
        if box.exec() != QMessageBox.Ok:
            return
        self.ctrl.settings.gaming_fps_asked = True
        self.ctrl.save_settings()
        if request_fps_permission():
            QMessageBox.information(self.window(), "Turn on FPS",
                                    "Done. Sign out of Windows and back in, and FPS will show while you play.")

    # ------------------------------------------------------------- the game
    def _toggle(self) -> None:
        self.ctrl.gaming.toggle()
        self.refresh()

    def _not_game(self) -> None:
        self.ctrl.gaming.not_a_game()
        self.refresh()

    def _set_auto(self, on: bool) -> None:
        self.ctrl.settings.gaming_on = on
        self.ctrl.save_settings()
