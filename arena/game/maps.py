"""Symmetric map generation and map files.

Maps are always symmetric (rotational, mirrored left/right, or mirrored top/bottom),
so both teams face exactly the same situation. Bots are not told which symmetry is used.
"""

from __future__ import annotations

import json
import random
from collections import deque
from dataclasses import dataclass, field

from . import constants as C

SYMMETRIES = ("rotational", "mirror_x", "mirror_y")


@dataclass
class MapData:
    width: int
    height: int
    walls: set = field(default_factory=set)          # {(x, y)}
    ore: dict = field(default_factory=dict)          # {(x, y): amount}
    hq: list = field(default_factory=list)           # [(x, y) team A, (x, y) team B]
    symmetry: str = "rotational"
    name: str = "generated"

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "symmetry": self.symmetry,
            "walls": sorted([x, y] for x, y in self.walls),
            "ore": sorted([x, y, a] for (x, y), a in self.ore.items()),
            "hq": [list(p) for p in self.hq],
        }

    @staticmethod
    def from_json(d: dict) -> "MapData":
        m = MapData(
            width=int(d["width"]),
            height=int(d["height"]),
            walls={(int(x), int(y)) for x, y in d.get("walls", [])},
            ore={(int(x), int(y)): int(a) for x, y, a in d.get("ore", [])},
            hq=[tuple(map(int, p)) for p in d["hq"]],
            symmetry=d.get("symmetry", "unknown"),
            name=d.get("name", "custom"),
        )
        m.validate()
        return m

    def validate(self) -> None:
        if len(self.hq) != 2:
            raise ValueError("map needs exactly two HQ positions")
        for x, y in list(self.walls) + list(self.ore) + list(self.hq):
            if not (0 <= x < self.width and 0 <= y < self.height):
                raise ValueError(f"map tile ({x}, {y}) is out of bounds")
        for p in self.hq:
            if p in self.walls:
                raise ValueError(f"HQ at {p} is on a wall")
        if not _reachable(self.width, self.height, self.walls, self.hq[0], self.hq[1]):
            raise ValueError("the two HQs are not connected")


def load_map(path: str) -> MapData:
    with open(path, encoding="utf-8") as f:
        return MapData.from_json(json.load(f))


def _mirror_fn(sym: str, w: int, h: int):
    if sym == "rotational":
        return lambda x, y: (w - 1 - x, h - 1 - y)
    if sym == "mirror_x":
        return lambda x, y: (w - 1 - x, y)
    return lambda x, y: (x, h - 1 - y)


_NEIGH8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


def _flood(w, h, walls, start):
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for dx, dy in _NEIGH8:
            n = (x + dx, y + dy)
            if 0 <= n[0] < w and 0 <= n[1] < h and n not in walls and n not in seen:
                seen.add(n)
                q.append(n)
    return seen


def _reachable(w, h, walls, a, b) -> bool:
    return b in _flood(w, h, walls, a)


def generate_map(seed: int, width: int | None = None, height: int | None = None) -> MapData:
    """Generate a random symmetric map. Same seed -> same map."""
    rng = random.Random(f"map:{seed}")
    for _attempt in range(200):
        w = width or rng.randint(C.MAP_MIN_SIZE, C.MAP_MAX_SIZE)
        h = height or rng.randint(C.MAP_MIN_SIZE, C.MAP_MAX_SIZE)
        sym = rng.choice(SYMMETRIES)
        mirror = _mirror_fn(sym, w, h)

        if sym == "mirror_x":
            ax, ay = rng.randint(2, w // 4), rng.randint(2, h - 3)
        elif sym == "mirror_y":
            ax, ay = rng.randint(2, w - 3), rng.randint(2, h // 4)
        else:
            ax, ay = rng.randint(2, w // 3), rng.randint(2, h - 3)
        bx, by = mirror(ax, ay)
        if (ax - bx) ** 2 + (ay - by) ** 2 < (0.55 * min(w, h)) ** 2:
            continue
        hqs = [(ax, ay), (bx, by)]

        def near_hq(x, y, r2=8):
            return any((x - hx) ** 2 + (y - hy) ** 2 <= r2 for hx, hy in hqs)

        # walls: random-walk blobs, mirrored
        walls: set = set()
        for _ in range(rng.randint(w * h // 110, w * h // 70)):
            x, y = rng.randrange(w), rng.randrange(h)
            for _ in range(rng.randint(3, 9)):
                for p in ((x, y), mirror(x, y)):
                    if not near_hq(*p):
                        walls.add(p)
                dx, dy = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
                x = min(w - 1, max(0, x + dx))
                y = min(h - 1, max(0, y + dy))

        if not _reachable(w, h, walls, hqs[0], hqs[1]):
            continue
        reach = _flood(w, h, walls, hqs[0])

        # ore: clusters, one guaranteed near each HQ, mirrored
        ore: dict = {}

        def add_cluster(cx, cy):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    x, y = cx + dx, cy + dy
                    if not (0 <= x < w and 0 <= y < h) or rng.random() > 0.6:
                        continue
                    amount = rng.randint(C.ORE_DEPOSIT_MIN, C.ORE_DEPOSIT_MAX)
                    for p in ((x, y), mirror(x, y)):
                        if p not in walls and p in reach and p not in hqs:
                            ore[p] = amount

        angle_dirs = [(dx, dy) for dx in range(-7, 8) for dy in range(-7, 8) if 16 <= dx * dx + dy * dy <= 49]
        dx, dy = rng.choice(angle_dirs)
        add_cluster(min(w - 2, max(1, ax + dx)), min(h - 2, max(1, ay + dy)))
        for _ in range(rng.randint(4, 7)):
            add_cluster(rng.randrange(1, w - 1), rng.randrange(1, h - 1))
        if len(ore) < 12:
            continue

        m = MapData(w, h, walls, ore, hqs, sym, name=f"gen-{seed}")
        return m

    # fallback: open map
    w = width or 30
    h = height or 30
    return MapData(w, h, set(), {(5, 5): 100, (w - 6, h - 6): 100}, [(3, 3), (w - 4, h - 4)], "rotational", f"gen-{seed}")
