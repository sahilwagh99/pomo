"""The focus garden: a sakura tree that grows with every pomodoro, and the outfits it unlocks.

The tree's size follows your lifetime pomodoros; its blossoms are the ones in the range you're
looking at (one per pomodoro). Milestones unlock new outfits for Pomo.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .config import OUTFITS, QUEST_UNLOCKS, UNLOCKS

STAGES = ((0, "Sprout"), (5, "Sapling"), (25, "Young tree"), (100, "Sakura tree"), (250, "Grand sakura"))
TRUNK = QColor("#7d5148")
BLOSSOMS = ("#fbd0d7", "#f6b3c4", "#f9c2cd", "#fde3e8")
LEAF = QColor("#a6cf92")


def stage(lifetime: int) -> tuple[int, str]:
    index = max(i for i, (at, _name) in enumerate(STAGES) if lifetime >= at)
    return index, STAGES[index][1]


def is_unlocked(outfit: str, lifetime: int, quests: int = 0) -> bool:
    """Garden outfits need lifetime pomodoros; quest outfits need daily quests finished."""
    return lifetime >= UNLOCKS.get(outfit, 0) and quests >= QUEST_UNLOCKS.get(outfit, 0)


def unlock_hint(outfit: str) -> str:
    """'(25 pomodoros)' or '(15 quests)': what it takes to earn an outfit."""
    if outfit in QUEST_UNLOCKS:
        return f"{QUEST_UNLOCKS[outfit]} quests"
    return f"{UNLOCKS[outfit]} pomodoros" if outfit in UNLOCKS else ""


def next_unlock(lifetime: int) -> tuple[str, int] | None:
    """The next outfit to earn and the lifetime pomodoros it needs."""
    ahead = sorted((at, key) for key, at in UNLOCKS.items() if at > lifetime)
    return (ahead[0][1], ahead[0][0]) if ahead else None


def newly_unlocked(lifetime: int, seen: list[str]) -> list[str]:
    return [key for key, at in UNLOCKS.items() if lifetime >= at and key not in seen]


def progress_text(lifetime: int) -> str:
    upcoming = next_unlock(lifetime)
    if upcoming is None:
        return "Every outfit unlocked. Pomo has a full wardrobe!"
    key, at = upcoming
    left = at - lifetime
    return f"{left} more {'pomodoro' if left == 1 else 'pomodoros'} to unlock the {OUTFITS[key].lower()}."


@dataclass
class _Branch:
    a: QPointF
    b: QPointF
    width: float
    depth: int


def grow(stage_index: int, base: QPointF, height: float, seed: int = 6) -> list[_Branch]:
    """The tree for a growth stage: a trunk that forks a little more at each stage."""
    rng = random.Random(seed)
    depth = (1, 2, 4, 5, 6)[stage_index]
    trunk = height * (0.3, 0.42, 0.34, 0.29, 0.25)[stage_index]
    branches: list[_Branch] = []

    def fork(a: QPointF, angle: float, length: float, width: float, level: int) -> None:
        b = QPointF(a.x() + math.cos(angle) * length, a.y() - math.sin(angle) * length)
        branches.append(_Branch(a, b, width, level))
        if level >= depth:
            return
        spread = rng.uniform(0.4, 0.62)
        for side in (-1, 1):
            if level >= 4 and rng.random() < 0.1:
                continue  # a gap here and there looks more like a real tree
            fork(b, angle + side * spread + rng.uniform(-0.1, 0.1), length * rng.uniform(0.68, 0.78),
                 max(1.2, width * 0.68), level + 1)

    fork(base, math.pi / 2 + rng.uniform(-0.05, 0.05), trunk, max(3.0, height * 0.045), 1)
    return branches


def blossom_spots(branches: list[_Branch], count: int, seed: int = 11) -> list[tuple[QPointF, float]]:
    """Where blossoms open: branch tips first, then along the smaller branches."""
    rng = random.Random(seed)
    if not branches or count <= 0:
        return []
    top = max(b.depth for b in branches)
    tips = [b for b in branches if b.depth == top]
    small = [b for b in branches if b.depth >= max(2, top - 2)]
    spots = []
    for i in range(count):
        branch = tips[i % len(tips)] if i < len(tips) * 2 else rng.choice(small)
        t = 1.0 if i < len(tips) else rng.uniform(0.35, 1.0)
        point = QPointF(branch.a.x() + (branch.b.x() - branch.a.x()) * t + rng.uniform(-6, 6),
                        branch.a.y() + (branch.b.y() - branch.a.y()) * t + rng.uniform(-6, 6))
        spots.append((point, rng.uniform(3.2, 5.2)))
    return spots


class GardenWidget(QWidget):
    def __init__(self, theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.lifetime = 0
        self.blossoms = 0
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, lifetime: int, blossoms: int) -> None:
        self.lifetime, self.blossoms = lifetime, blossoms
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        ground_y = h - 26
        # a soft hill
        hill = QPainterPath(QPointF(0, h))
        hill.lineTo(0, ground_y + 8)
        hill.cubicTo(w * 0.3, ground_y - 10, w * 0.7, ground_y - 10, w, ground_y + 8)
        hill.lineTo(w, h)
        hill.closeSubpath()
        green = QColor(LEAF)
        green.setAlpha(110 if not self.theme.dark else 80)
        p.setPen(Qt.NoPen)
        p.setBrush(green)
        p.drawPath(hill)

        index, _name = stage(self.lifetime)
        base = QPointF(w / 2, ground_y - 2)
        if index == 0:  # a sprout: two leaves
            p.setPen(QPen(QColor("#6f9d60"), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(base, QPointF(base.x(), base.y() - 34))
            p.setPen(Qt.NoPen)
            p.setBrush(LEAF)
            for side in (-1, 1):
                p.save()
                p.translate(base.x(), base.y() - 30)
                p.rotate(side * 40)
                p.drawEllipse(QRectF(-7 + side * 9, -6, 18, 10))
                p.restore()
            p.end()
            return
        branches = grow(index, base, h - 40)
        for branch in sorted(branches, key=lambda b: -b.width):
            p.setPen(QPen(TRUNK, branch.width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(branch.a, branch.b)
        spots = blossom_spots(branches, min(self.blossoms, 160))
        rng = random.Random(3)
        for point, r in spots:
            color = QColor(rng.choice(BLOSSOMS))
            p.setPen(QPen(QColor(232, 160, 176), 0.8))
            p.setBrush(color)
            p.drawEllipse(point, r, r)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 170))
            p.drawEllipse(QPointF(point.x() - r * 0.3, point.y() - r * 0.3), r * 0.32, r * 0.32)
        for k in range(min(len(spots) // 6, 14)):  # a few petals on the grass
            x = w / 2 + rng.uniform(-w * 0.32, w * 0.32)
            y = ground_y + rng.uniform(-2, 12)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(rng.choice(BLOSSOMS)))
            p.save()
            p.translate(x, y)
            p.rotate(rng.uniform(0, 180))
            p.drawEllipse(QRectF(-3, -1.8, 6, 3.6))
            p.restore()
        p.end()
