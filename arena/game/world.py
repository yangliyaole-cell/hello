"""Authoritative game state and rules for "Ore Rush" v0.

Bots never see these objects. They only get a RobotController, whose methods validate
every request against this state.
"""

from __future__ import annotations

import random

from . import constants as C
from .maps import MapData
from .types import RobotType, Team

TEAMS = (Team.A, Team.B)
_HQ = RobotType.HQ
_ID_MULTIPLIERS = (7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)

_OFFSET_CACHE: dict[int, list] = {}


def offsets_within(radius_sq: int) -> list:
    """(dx, dy) offsets with dx^2+dy^2 <= radius_sq, nearest first (ties in a fixed order)."""
    o = _OFFSET_CACHE.get(radius_sq)
    if o is None:
        r = int(radius_sq ** 0.5) + 1
        o = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if dx * dx + dy * dy <= radius_sq]
        o.sort(key=lambda t: (t[0] * t[0] + t[1] * t[1], t[1], t[0]))
        _OFFSET_CACHE[radius_sq] = o
    return o


def _new_stats() -> dict:
    return {
        "built": {t.name: 0 for t in RobotType.buildable()},
        "lost": {t.name: 0 for t in RobotType.buildable()},
        "ore_mined": 0,
        "ore_spent": 0,
        "damage_dealt": 0,
        "kills": 0,
        # filled in by the engine
        "turns": 0,
        "instructions": 0,
        "exceptions": 0,
        "budget_exceeded": 0,
        "timeouts": 0,
    }


class Robot:
    __slots__ = (
        "id", "team", "type", "x", "y", "health", "move_cd", "action_cd", "alive",
        "spawn_index", "indicator", "dots", "lines", "used",
    )


class GameWorld:
    def __init__(self, mapdata: MapData, seed: int, max_rounds: int = C.MAX_ROUNDS):
        self.map = mapdata
        self.w = w = mapdata.width
        self.h = h = mapdata.height
        n = w * h
        self.wall = bytearray(n)
        for x, y in mapdata.walls:
            self.wall[y * w + x] = 1
        self.ore = [0] * n
        for (x, y), a in mapdata.ore.items():
            self.ore[y * w + x] = a
        self.occ: list = [None] * n
        self.robots: dict[int, Robot] = {}
        self.order: list[Robot] = []
        self.round = 0
        self.max_rounds = max_rounds
        self.team_ore = [C.STARTING_ORE, C.STARTING_ORE]
        self.shared = ([0] * C.SHARED_ARRAY_SIZE, [0] * C.SHARED_ARRAY_SIZE)
        self.unit_count = [0, 0]
        self.winner: int | None = None
        self.reason = ""
        self.events: list = []
        self.ore_changes: dict[int, int] = {}
        self.stats = [_new_stats(), _new_stats()]
        self.rng = random.Random(f"world:{seed}")
        # Robot ids: a seeded permutation of 1000..9999 so ids carry no information.
        self._id_mul = self.rng.choice(_ID_MULTIPLIERS)
        self._id_add = self.rng.randrange(9000)
        # Sensing returns nearest first. Ties within a distance ring are broken in a
        # per-match random order: a fixed order (say south-west first) would favour one
        # side of a mirrored map. Precomputed here because engine code must not call
        # the metered `random` module during a bot's turn.
        max_r2 = max(t.vision_radius_sq for t in RobotType.all())
        self.offsets = [self._shuffled_rings(r2) for r2 in range(max_r2 + 1)]
        self.spawned = 0
        self.hq: list[Robot] = [self.spawn(t, _HQ, *mapdata.hq[t]) for t in (0, 1)]

    def _shuffled_rings(self, r2: int) -> list:
        out, ring, last = [], [], None
        for off in offsets_within(r2):
            d = off[0] * off[0] + off[1] * off[1]
            if d != last and ring:
                self.rng.shuffle(ring)
                out.extend(ring)
                ring = []
            ring.append(off)
            last = d
        self.rng.shuffle(ring)
        out.extend(ring)
        return out

    # ------------------------------------------------------------------ lifecycle

    def spawn(self, team: int, rtype: RobotType, x: int, y: int) -> Robot:
        r = Robot()
        r.id = 1000 + (self._id_mul * self.spawned + self._id_add) % 9000
        r.team = team
        r.type = rtype
        r.x, r.y = x, y
        r.health = rtype.health
        r.move_cd = r.action_cd = 0
        r.alive = True
        r.spawn_index = self.spawned
        r.indicator = None
        r.dots = None
        r.lines = None
        r.used = 0
        self.spawned += 1
        self.robots[r.id] = r
        self.order.append(r)
        self.occ[y * self.w + x] = r
        if rtype is not _HQ:
            self.unit_count[team] += 1
            self.stats[team]["built"][rtype.name] += 1
        self.events.append(["spawn", r.id, x, y])
        return r

    def begin_round(self) -> None:
        self.round += 1
        self.events = []
        self.ore_changes = {}
        self.team_ore[0] += C.PASSIVE_INCOME
        self.team_ore[1] += C.PASSIVE_INCOME
        dec = C.COOLDOWN_PER_ROUND
        for r in self.order:
            r.move_cd = r.move_cd - dec if r.move_cd > dec else 0
            r.action_cd = r.action_cd - dec if r.action_cd > dec else 0
            r.used = 0

    def turn_order(self) -> list[Robot]:
        """Robots act one at a time, in spawn order (so the two teams interleave)."""
        return [r for r in self.order if r.alive]

    def end_round(self) -> None:
        self.order = [r for r in self.order if r.alive]
        if self.winner is None and self.round >= self.max_rounds:
            self._tiebreak()

    @property
    def over(self) -> bool:
        return self.winner is not None

    def kill(self, r: Robot) -> None:
        r.alive = False
        r.health = 0
        self.occ[r.y * self.w + r.x] = None
        del self.robots[r.id]
        self.events.append(["die", r.id, r.x, r.y])
        if r.type is _HQ:
            if self.winner is None:
                self.winner = 1 - r.team
                self.reason = f"destroyed the enemy HQ in round {self.round}"
        else:
            self.unit_count[r.team] -= 1
            self.stats[r.team]["lost"][r.type.name] += 1

    def _tiebreak(self) -> None:
        def army(t):
            return sum(r.type.cost for r in self.order if r.alive and r.team == t)

        keys = [
            ("HQ health", self.hq[0].health, self.hq[1].health),
            ("army value", army(0), army(1)),
            ("ore in the bank", self.team_ore[0], self.team_ore[1]),
            ("total ore mined", self.stats[0]["ore_mined"], self.stats[1]["ore_mined"]),
        ]
        for label, a, b in keys:
            if a != b:
                self.winner = 0 if a > b else 1
                self.reason = f"round limit reached; won on {label} ({max(a, b)} vs {min(a, b)})"
                return
        self.winner = self.rng.randrange(2)
        self.reason = "round limit reached; every tiebreak was equal, decided by a seeded coin flip"

    # ------------------------------------------------------------------ queries

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def robot_at(self, x: int, y: int):
        return self.occ[y * self.w + x]

    def sense_robots(self, robot: Robot, radius_sq: int, team: int | None) -> list[Robot]:
        out = []
        w, h, occ = self.w, self.h, self.occ
        x0, y0 = robot.x, robot.y
        for dx, dy in self.offsets[radius_sq]:
            x = x0 + dx
            y = y0 + dy
            if 0 <= x < w and 0 <= y < h:
                o = occ[y * w + x]
                if o is not None and o is not robot and (team is None or o.team == team):
                    out.append(o)
        return out

    def sense_ore(self, robot: Robot, radius_sq: int) -> list[tuple[int, int, int]]:
        out = []
        w, h, ore = self.w, self.h, self.ore
        x0, y0 = robot.x, robot.y
        for dx, dy in self.offsets[radius_sq]:
            x = x0 + dx
            y = y0 + dy
            if 0 <= x < w and 0 <= y < h:
                a = ore[y * w + x]
                if a:
                    out.append((x, y, a))
        return out

    # ------------------------------------------------------------------ rule checks
    # Each returns None if the action is legal, otherwise a human-readable reason.

    def why_not_move(self, r: Robot, dx: int, dy: int):
        if not r.type.can_move:
            return f"{r.type.name} cannot move"
        if r.move_cd >= C.COOLDOWN_THRESHOLD:
            return f"movement cooldown is {r.move_cd} (must be < {C.COOLDOWN_THRESHOLD})"
        if dx == 0 and dy == 0:
            return "cannot move to Direction.CENTER"
        x, y = r.x + dx, r.y + dy
        if not self.in_bounds(x, y):
            return f"({x}, {y}) is off the map"
        if self.wall[y * self.w + x]:
            return f"({x}, {y}) is a wall"
        if self.occ[y * self.w + x] is not None:
            return f"({x}, {y}) is occupied by another robot"
        return None

    def why_not_attack(self, r: Robot, x: int, y: int):
        if not r.type.can_attack:
            return f"{r.type.name} cannot attack"
        if r.action_cd >= C.COOLDOWN_THRESHOLD:
            return f"action cooldown is {r.action_cd} (must be < {C.COOLDOWN_THRESHOLD})"
        if (x - r.x) ** 2 + (y - r.y) ** 2 > r.type.action_radius_sq:
            return f"({x}, {y}) is outside the attack radius (radius_sq {r.type.action_radius_sq})"
        if not self.in_bounds(x, y):
            return f"({x}, {y}) is off the map"
        t = self.occ[y * self.w + x]
        if t is None:
            return f"no robot at ({x}, {y})"
        if t.team == r.team:
            return f"the robot at ({x}, {y}) is on your own team"
        return None

    def why_not_mine(self, r: Robot, x: int, y: int):
        if not r.type.can_mine:
            return f"{r.type.name} cannot mine"
        if r.action_cd >= C.COOLDOWN_THRESHOLD:
            return f"action cooldown is {r.action_cd} (must be < {C.COOLDOWN_THRESHOLD})"
        if (x - r.x) ** 2 + (y - r.y) ** 2 > r.type.action_radius_sq:
            return f"({x}, {y}) is not adjacent (mining radius_sq {r.type.action_radius_sq})"
        if not self.in_bounds(x, y):
            return f"({x}, {y}) is off the map"
        if self.ore[y * self.w + x] <= 0:
            return f"no ore at ({x}, {y})"
        return None

    def why_not_build(self, r: Robot, rtype: RobotType, dx: int, dy: int):
        if not r.type.can_build:
            return f"{r.type.name} cannot build"
        if rtype not in RobotType.buildable():
            return f"cannot build {rtype.name}"
        if r.action_cd >= C.COOLDOWN_THRESHOLD:
            return f"action cooldown is {r.action_cd} (must be < {C.COOLDOWN_THRESHOLD})"
        if self.team_ore[r.team] < rtype.cost:
            return f"not enough ore ({self.team_ore[r.team]} < {rtype.cost})"
        if self.unit_count[r.team] >= C.MAX_UNITS_PER_TEAM:
            return f"unit limit reached ({C.MAX_UNITS_PER_TEAM})"
        if dx == 0 and dy == 0:
            return "cannot build at Direction.CENTER"
        x, y = r.x + dx, r.y + dy
        if not self.in_bounds(x, y):
            return f"({x}, {y}) is off the map"
        if self.wall[y * self.w + x]:
            return f"({x}, {y}) is a wall"
        if self.occ[y * self.w + x] is not None:
            return f"({x}, {y}) is occupied"
        return None

    # ------------------------------------------------------------------ effects (call only after a check)

    def do_move(self, r: Robot, dx: int, dy: int) -> None:
        w = self.w
        self.occ[r.y * w + r.x] = None
        r.x += dx
        r.y += dy
        self.occ[r.y * w + r.x] = r
        r.move_cd += r.type.move_cooldown

    def do_attack(self, r: Robot, x: int, y: int) -> None:
        t = self.occ[y * self.w + x]
        r.action_cd += r.type.action_cooldown
        dmg = min(r.type.damage, t.health)
        t.health -= dmg
        st = self.stats[r.team]
        st["damage_dealt"] += dmg
        self.events.append(["atk", r.id, x, y])
        if t.health <= 0:
            st["kills"] += 1
            self.kill(t)

    def do_mine(self, r: Robot, x: int, y: int) -> None:
        i = y * self.w + x
        amount = min(C.MINE_AMOUNT, self.ore[i])
        self.ore[i] -= amount
        self.ore_changes[i] = self.ore[i]
        self.team_ore[r.team] += amount
        self.stats[r.team]["ore_mined"] += amount
        r.action_cd += r.type.action_cooldown
        self.events.append(["mine", r.id, x, y])

    def do_build(self, r: Robot, rtype: RobotType, dx: int, dy: int) -> Robot:
        self.team_ore[r.team] -= rtype.cost
        self.stats[r.team]["ore_spent"] += rtype.cost
        r.action_cd += r.type.action_cooldown
        return self.spawn(r.team, rtype, r.x + dx, r.y + dy)

    # ------------------------------------------------------------------ replay

    def snapshot(self) -> dict:
        w = self.w
        alive = [r for r in self.order if r.alive]
        snap: dict = {
            "robots": [[r.id, r.team, r.type.index, r.x, r.y, r.health, r.used] for r in alive],
            "ore": list(self.team_ore),
        }
        if self.events:
            snap["events"] = self.events
        if self.ore_changes:
            snap["orech"] = [[i % w, i // w, a] for i, a in self.ore_changes.items()]
        ind = [[r.id, r.indicator] for r in alive if r.indicator]
        if ind:
            snap["ind"] = ind
        dots = [[r.id, *d] for r in alive if r.dots for d in r.dots]
        if dots:
            snap["dots"] = dots
        lines = [[r.id, *ln] for r in alive if r.lines for ln in r.lines]
        if lines:
            snap["lines"] = lines
        return snap
