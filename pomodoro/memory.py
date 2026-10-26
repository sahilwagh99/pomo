"""Memory Match: a break-time game with Pomo's wardrobe. Sixteen cards, eight outfits in pairs;
turn two over, keep them if Pomo is wearing the same thing on both. Clear the board in as few
moves as you can.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from .config import OUTFITS
from .icons import draw_glyph
from .pomo import BODY_RY, Pomo

COLS, ROWS = 4, 4
PEEK_S = 0.9       # a mismatched pair stays up this long
FLIP_S = 0.22


@dataclass
class Card:
    outfit: str
    up: bool = False
    matched: bool = False
    turn: float = 1.0  # flip animation: 1 = settled, below 1 = turning


class MemoryMatch:
    """The game's state and rules, without widgets."""

    def __init__(self, rng: random.Random | None = None, outfits: list[str] | None = None):
        self.rng = rng or random.Random()
        self.outfits = outfits or [k for k in OUTFITS if k != "none"]
        self.reset()

    def reset(self) -> None:
        chosen = self.rng.sample(self.outfits, COLS * ROWS // 2)
        faces = chosen * 2
        self.rng.shuffle(faces)
        self.cards = [Card(outfit) for outfit in faces]
        self.moves = 0
        self.running = False
        self.elapsed = 0.0
        self._peek = 0.0
        self._pending: list[int] = []

    def start(self) -> None:
        self.reset()
        self.running = True

    @property
    def pairs_left(self) -> int:
        return sum(1 for c in self.cards if not c.matched) // 2

    def flip(self, index: int) -> list[str]:
        """Turn a card over; returns events: 'flip', 'match', 'miss', 'win'."""
        if not self.running or not 0 <= index < len(self.cards):
            return []
        card = self.cards[index]
        if card.up or card.matched or len(self._pending) >= 2:
            return []
        card.up, card.turn = True, 0.0
        self._pending.append(index)
        if len(self._pending) < 2:
            return ["flip"]
        self.moves += 1
        a, b = (self.cards[i] for i in self._pending)
        if a.outfit == b.outfit:
            a.matched = b.matched = True
            self._pending = []
            if self.pairs_left == 0:
                self.running = False
                return ["match", "win"]
            return ["match"]
        self._peek = PEEK_S
        return ["miss"]

    def update(self, dt: float) -> None:
        dt = max(0.0, min(dt, 0.1))
        if self.running:
            self.elapsed += dt
        for card in self.cards:
            card.turn = min(1.0, card.turn + dt / FLIP_S)
        if self._peek > 0:
            self._peek -= dt
            if self._peek <= 0:
                for i in self._pending:
                    self.cards[i].up, self.cards[i].turn = False, 0.0
                self._pending = []


class MemoryView:
    """Memory Match inside the game window."""

    key = "memory"
    title = "Memory Match"
    blurb = ("Find the pairs of Pomo's outfits.", "Turn two cards at a time.", "Clear the board in few moves.")
    TOP = 64
    PAD = 14

    def __init__(self, host):
        self.host = host
        self.game = MemoryMatch()
        self.state = "ready"
        self.new_best = False
        self._faces: dict[str, QPixmap] = {}
        self._hover = -1

    def best_text(self) -> str:
        best = self.host.ctrl.settings.memory_best
        return f"Best: {best} moves" if best else "Not played yet"

    def start(self) -> None:
        self.game.start()
        self.state = "playing"
        self.new_best = False

    def stop(self) -> None:
        if self.state == "playing":
            self.game.running = False
            self.state = "ready"

    def tick(self, dt: float) -> None:
        self.game.update(dt)

    def _cell(self, index: int) -> QRectF:
        w = self.host.WIDTH - 2 * self.PAD
        h = self.host.HEIGHT - self.TOP - 2 * self.PAD
        cw, ch = w / COLS, h / ROWS
        col, row = index % COLS, index // COLS
        return QRectF(self.PAD + col * cw + 5, self.TOP + self.PAD + row * ch + 5, cw - 10, ch - 10)

    def _index_at(self, pos: QPointF) -> int:
        return next((i for i in range(len(self.game.cards)) if self._cell(i).contains(pos)), -1)

    def mouse_press(self, pos: QPointF) -> None:
        if self.state != "playing":
            self.start()
            return
        events = self.game.flip(self._index_at(pos))
        if "win" in events:
            self._finish()

    def mouse_move(self, pos: QPointF) -> None:
        self._hover = self._index_at(pos)

    def key_press(self, key) -> bool:
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and self.state != "playing":
            self.start()
            return True
        return False

    def key_release(self, key) -> None:
        pass

    def _finish(self) -> None:
        self.state = "over"
        s = self.host.ctrl.settings
        if not s.memory_best or self.game.moves < s.memory_best:
            s.memory_best = self.game.moves
            self.host.ctrl.save_settings()
            self.new_best = True
        self.host.round_over(self.key)

    def _face(self, outfit: str, size: int) -> QPixmap:
        key = f"{outfit}-{size}"
        if key not in self._faces:
            pm = QPixmap(size * 2, size * 2)
            pm.setDevicePixelRatio(2.0)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            pomo = Pomo(random.Random(1))
            pomo.outfit = outfit
            pomo._next_blink = 99
            body = size * 0.62
            pomo.paint(painter, QPointF(size / 2, size * 0.62 - body * BODY_RY * 0.2), body, ground_shadow=False)
            painter.end()
            self._faces[key] = pm
        return self._faces[key]

    def paint(self, p: QPainter, t, w: int, h: int) -> None:
        accent = QColor(t.focus)
        for i, card in enumerate(self.game.cards):
            cell = self._cell(i)
            squeeze = abs(math.cos(math.pi * card.turn)) if card.turn < 1 else 1.0
            showing_face = card.up if card.turn >= 0.5 else not card.up
            p.save()
            p.translate(cell.center())
            p.scale(max(0.04, squeeze), 1.0)
            rect = QRectF(-cell.width() / 2, -cell.height() / 2, cell.width(), cell.height())
            if showing_face:
                p.setPen(QPen(QColor(accent if card.matched else t.orb_edge), 2 if card.matched else 1))
                p.setBrush(QColor(t.surface))
                p.drawRoundedRect(rect, 12, 12)
                size = int(min(rect.width(), rect.height()))
                p.drawPixmap(QPointF(-size / 2, -size / 2 + 4), self._face(card.outfit, size))
            else:
                hot = i == self._hover and self.state == "playing"
                p.setPen(QPen(QColor(t.orb_edge), 1))
                p.setBrush(QColor(t.hover if hot else t.raised))
                p.drawRoundedRect(rect, 12, 12)
                draw_glyph(p, "petal", QPointF(0, 0), min(rect.width(), rect.height()) * 0.36, QColor(t.ink_2))
            p.restore()

    def hud(self) -> tuple[str, str]:
        g = self.game
        return f"{g.moves} moves", f"{g.pairs_left} pairs left"

    def overlay(self) -> tuple[str, list[str], str]:
        s = self.host.ctrl.settings
        if self.state == "ready":
            return self.title, list(self.blurb), "Click to play"
        seconds = int(self.game.elapsed)
        title = "New best!" if self.new_best else "All matched!"
        return title, [f"{self.game.moves} moves in {seconds // 60}:{seconds % 60:02d}.",
                       f"Best: {s.memory_best} moves", ""], "Click to play again"
