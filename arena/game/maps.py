"""Symmetric map generation and map files for "Frontier".

Maps are symmetric (rotational, mirrored left/right, or mirrored top/bottom) so both
teams face the same situation. Bots are not told which symmetry is used.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from . import constants as C

SYMMETRIES = ("rotational", "mirror_x", "mirror_y")


@dataclass
class MapData:
    width: int
    height: int
    hq: list                                   # [(x, y) team A, (x, y) team B]
    rich: set = field(default_factory=set)     # {(x, y)} resource-rich tiles
    walls: set = field(default_factory=set)    # {(x, y)} (none in generated maps yet)
    symmetry: str = "rotational"
    name: str = "generated"

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "symmetry": self.symmetry,
            "hq": [list(p) for p in self.hq],
            "rich": sorted([x, y] for x, y in self.rich),
            "walls": sorted([x, y] for x, y in self.walls),
        }

    @staticmethod
    def from_json(d: dict) -> "MapData":
        m = MapData(
            width=int(d["width"]),
            height=int(d["height"]),
            hq=[tuple(map(int, p)) for p in d["hq"]],
            rich={(int(x), int(y)) for x, y in d.get("rich", [])},
            walls={(int(x), int(y)) for x, y in d.get("walls", [])},
            symmetry=d.get("symmetry", "unknown"),
            name=d.get("name", "custom"),
        )
        m.validate()
        return m

    def validate(self) -> None:
        if len(self.hq) != 2:
            raise ValueError("map needs exactly two HQ positions")
        for x, y in self.hq:
            if not (1 <= x < self.width - 1 and 1 <= y < self.height - 1):
                raise ValueError(f"HQ ({x}, {y}) must have all 8 neighbours on the map")
        (ax, ay), (bx, by) = self.hq
        if max(abs(ax - bx), abs(ay - by)) < 6:
            raise ValueError("HQs are too close")
        camps = {(x + dx, y + dy) for x, y in self.hq for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        for p in list(self.rich) + list(self.walls):
            if not (0 <= p[0] < self.width and 0 <= p[1] < self.height):
                raise ValueError(f"tile {p} is out of bounds")
        if camps & self.walls:
            raise ValueError("walls overlap a starting camp")


def load_map(path: str) -> MapData:
    with open(path, encoding="utf-8") as f:
        return MapData.from_json(json.load(f))


def _mirror_fn(sym: str, w: int, h: int):
    if sym == "rotational":
        return lambda x, y: (w - 1 - x, h - 1 - y)
    if sym == "mirror_x":
        return lambda x, y: (w - 1 - x, y)
    return lambda x, y: (x, h - 1 - y)


_N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def generate_map(seed: int, width: int | None = None, height: int | None = None) -> MapData:
    """Generate a random symmetric map. Same seed -> same map."""
    rng = random.Random(f"map:{seed}")
    w = width or C.MAP_WIDTH
    h = height or C.MAP_HEIGHT
    for _attempt in range(500):
        sym = rng.choice(SYMMETRIES)
        mirror = _mirror_fn(sym, w, h)
        if sym == "mirror_x":
            ax, ay = rng.randint(3, w // 5), rng.randint(3, h - 4)
        elif sym == "mirror_y":
            ax, ay = rng.randint(3, w - 4), rng.randint(3, h // 5)
        else:
            ax, ay = rng.randint(3, w // 4), rng.randint(3, h - 4)
        bx, by = mirror(ax, ay)
        if max(abs(ax - bx), abs(ay - by)) < 0.6 * min(w, h):
            continue
        hqs = [(ax, ay), (bx, by)]

        def far_from_hqs(p, d=7):
            return all(max(abs(p[0] - hx), abs(p[1] - hy)) >= d for hx, hy in hqs)

        def grow(start: set, size: int, symmetric: bool) -> set:
            cells = set(start)
            for _ in range(200):
                if len(cells) >= size:
                    break
                cx, cy = rng.choice(sorted(cells))
                dx, dy = rng.choice(_N4)
                n = (cx + dx, cy + dy)
                if not (1 <= n[0] < w - 1 and 1 <= n[1] < h - 1) or not far_from_hqs(n):
                    continue
                cells.add(n)
                if symmetric:
                    cells.add(mirror(*n))
            return cells

        rich: set = set()
        lo, hi = C.RICH_CLUSTER_SIZE
        # one mirrored pair of clusters
        for _ in range(100):
            seed_cell = (rng.randrange(2, w - 2), rng.randrange(2, h - 2))
            m = mirror(*seed_cell)
            if far_from_hqs(seed_cell, 9) and max(abs(seed_cell[0] - m[0]), abs(seed_cell[1] - m[1])) >= 12:
                break
        else:
            continue
        cluster = grow({seed_cell}, rng.randint(lo, hi), symmetric=False)
        if max(abs(x - mx) for (x, _), (mx, _) in zip(sorted(cluster), sorted(cluster))) > 4:
            continue
        rich |= cluster | {mirror(*p) for p in cluster}
        if len(rich) != 2 * len(cluster):
            continue  # cluster touched its own mirror image
        # optionally a self-symmetric cluster in the middle
        if rng.choice(C.RICH_CLUSTERS) == 3:
            cx, cy = w // 2, h // 2
            if sym == "mirror_x":
                cy = rng.randrange(h // 3, 2 * h // 3)
            elif sym == "mirror_y":
                cx = rng.randrange(w // 3, 2 * w // 3)
            middle = grow({(cx, cy), mirror(cx, cy)}, lo, symmetric=True)
            if middle & rich:
                continue
            rich |= middle
        m = MapData(w, h, hqs, rich, set(), sym, name=f"gen-{seed}")
        m.validate()
        return m
    raise RuntimeError(f"could not generate a map for seed {seed}")
