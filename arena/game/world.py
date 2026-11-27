"""Authoritative game state and rules for "Frontier".

Bots never see these objects; they act through a RobotController, whose methods
validate every request against this state.

Time: turn 1 is team A, turn 2 team B, turn 3 team A, ... A round is two turns.
"""

from __future__ import annotations

import math
import random

from . import constants as C
from .maps import MapData
from .types import Direction, Team

TEAMS = (Team.A, Team.B)
_ID_MULTIPLIERS = (7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 49, 53, 59, 61)
_NEIGH8 = tuple((dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)

# tile states used in replays
BLANK, OWN_A, OWN_B, RUIN, WALL = 0, 1, 2, 3, 4


def _vision_offsets(r: int) -> tuple:
    """(dx, dy, steps): the target is visible unless some step is fully blocked.
    Each step lists the candidate cells the sight line passes through at that point."""
    out = []
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx == 0 and dy == 0:
                continue
            d = max(abs(dx), abs(dy))
            steps = []
            for k in range(1, d):
                px, py = dx * k / d, dy * k / d
                cands = tuple(sorted({(math.floor(px), math.floor(py)), (math.floor(px), math.ceil(py)),
                                      (math.ceil(px), math.floor(py)), (math.ceil(px), math.ceil(py))}))
                steps.append(cands)
            out.append((dx, dy, tuple(steps)))
    return tuple(out)


VISION_OFFSETS = _vision_offsets(C.VISION_RADIUS)


class Agent:
    """A squad, or a team's HQ (is_hq=True, n = garrison, never moves)."""

    __slots__ = (
        "id", "team", "x", "y", "n", "move_left", "alive", "is_hq", "spawn_index", "memo", "inbox",
        "inbox_start", "last_move", "indicator", "dots", "lines", "used",
    )

    def __init__(self, aid: int, team: int, x: int, y: int, n: int, is_hq: bool, spawn_index: int):
        self.id = aid
        self.team = team
        self.x, self.y = x, y
        self.n = n
        self.move_left = 0
        self.alive = True
        self.is_hq = is_hq
        self.spawn_index = spawn_index
        self.memo = None
        self.inbox: list = []
        self.inbox_start = 0
        self.last_move = None
        self.indicator = None
        self.dots = None
        self.lines = None
        self.used = 0


class Occupation:
    __slots__ = ("team", "idx", "total", "rounds", "ticks", "paid", "ruined", "start_turn")

    def __init__(self, team, idx, total, rounds, ruined, start_turn):
        self.team, self.idx, self.total, self.rounds = team, idx, total, rounds
        self.ticks = 0
        self.paid = 0
        self.ruined = ruined
        self.start_turn = start_turn

    def next_installment(self) -> int:
        """The total is spread evenly over the rounds; the remainder is paid one gold
        at a time in the first rounds."""
        base, extra = divmod(self.total, self.rounds)
        return base + (1 if self.ticks < extra else 0)

    @property
    def remaining(self) -> int:
        return self.total - self.paid


def occupation_rounds(total: int, ruined: bool) -> int:
    base, cap = (C.REPAIR_BASE, C.REPAIR_MAX_ROUNDS) if ruined else (C.CLAIM_BASE, C.CLAIM_MAX_ROUNDS)
    return max(1, min(cap, -(-base // total)))


def _new_stats() -> dict:
    return {
        "max_territory": 0, "squads_spawned": 0, "soldiers_spawned": 0, "soldiers_lost": 0,
        "enemy_soldiers_killed": 0, "tiles_claimed": 0, "tiles_repaired": 0, "tiles_razed": 0,
        "tiles_lost": 0, "occupations_started": 0, "occupations_failed": 0, "occupations_cancelled": 0,
        "gold_on_soldiers": 0, "gold_on_occupation": 0, "income_total": 0, "upkeep_total": 0,
        # filled in by the engine
        "turns": 0, "instructions": 0, "exceptions": 0, "budget_exceeded": 0, "timeouts": 0,
    }


class GameWorld:
    def __init__(self, mapdata: MapData, seed: int, max_rounds: int = C.MAX_ROUNDS):
        self.map = mapdata
        self.w = w = mapdata.width
        self.h = h = mapdata.height
        n = w * h
        self.owner = bytearray(n)              # 0 none, 1 team A, 2 team B
        self.rich = bytearray(n)
        self.wall = bytearray(n)
        for x, y in mapdata.rich:
            self.rich[y * w + x] = 1
        for x, y in mapdata.walls:
            self.wall[y * w + x] = 1
        self.ruins: dict[int, int] = {}        # idx -> rounds until it turns blank
        self.occ: dict[int, Occupation] = {}   # idx -> occupation in progress
        self.sq = ([None] * n, [None] * n)     # squad of each team on each cell
        self.squads: dict[int, Agent] = {}     # living squads by id
        self.order: list[Agent] = []           # squads in creation order
        self.gold = [C.STARTING_GOLD, C.STARTING_GOLD]
        self.tiles = [0, 0]
        self.rich_tiles = [0, 0]
        self.soldiers = [0, 0]                 # in squads (garrisons not included)
        self.squad_count = [0, 0]
        self.cover = ([0] * n, [0] * n)        # owned tiles within vision radius (territory vision)
        self.visible = (set(), set())          # cells seen by each team's territory
        self.board = ([0] * C.BOARD_SIZE, [0] * C.BOARD_SIZE)
        self.version = 0                       # bumped on every change that can affect vision
        self.territory_version = 0
        self._conn = [(-1, set()), (-1, set())]
        self.turn = 0
        self.round = 0
        self.active = 0
        self.max_rounds = max_rounds
        self.spawns_left = 0
        self.winner: int | None = None
        self.reason = ""
        self.stats = [_new_stats(), _new_stats()]
        self.rng = random.Random(f"world:{seed}")
        self._id_mul = self.rng.choice(_ID_MULTIPLIERS)
        self._id_add = self.rng.randrange(90000)
        self.spawned = 0
        self._reset_frame()

        self.hq: list[Agent] = []
        self.hq_idx = []
        for t, (x, y) in enumerate(mapdata.hq):
            hq = Agent(self._new_id(), t, x, y, C.HQ_GARRISON, True, self.spawned)
            self.spawned += 1
            self.hq.append(hq)
            self.hq_idx.append(y * w + x)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    self._set_owner((y + dy) * w + (x + dx), t + 1)
        self._reset_frame()

    # ------------------------------------------------------------------ helpers

    def _new_id(self) -> int:
        return 10000 + (self._id_mul * self.spawned + self._id_add) % 90000

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def tile_state(self, idx: int) -> int:
        if self.owner[idx]:
            return self.owner[idx]
        if idx in self.ruins:
            return RUIN
        if self.wall[idx]:
            return WALL
        return BLANK

    def neighbors8(self, idx: int):
        x, y = idx % self.w, idx // self.w
        for dx, dy in _NEIGH8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.w and 0 <= ny < self.h:
                yield ny * self.w + nx

    def enemy_adjacent(self, team: int, idx: int) -> bool:
        enemy = 2 - team  # owner code of the other team
        return any(self.owner[n] == enemy for n in self.neighbors8(idx))

    def _set_owner(self, idx: int, code: int) -> None:
        old = self.owner[idx]
        if old == code:
            return
        w, r = self.w, C.VISION_RADIUS
        x, y = idx % w, idx // w
        for c, delta in ((old, -1), (code, 1)):
            if not c:
                continue
            t = c - 1
            self.tiles[t] += delta
            if self.rich[idx]:
                self.rich_tiles[t] += delta
            cover, vis = self.cover[t], self.visible[t]
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < self.h:
                        j = ny * w + nx
                        cover[j] += delta
                        if cover[j] == 1 and delta == 1:
                            vis.add(j)
                        elif cover[j] == 0:
                            vis.discard(j)
        self.owner[idx] = code
        if code:
            self.ruins.pop(idx, None)
            st = self.stats[code - 1]
            st["max_territory"] = max(st["max_territory"], self.tiles[code - 1])
        self.version += 1
        self.territory_version += 1
        self.tile_changes[idx] = self.tile_state(idx)

    def connected(self, team: int) -> set:
        """Own tiles 8-connected to the HQ."""
        ver, cells = self._conn[team]
        if ver == self.territory_version:
            return cells
        code = team + 1
        start = self.hq_idx[team]
        cells = {start}
        stack = [start]
        while stack:
            i = stack.pop()
            for j in self.neighbors8(i):
                if self.owner[j] == code and j not in cells:
                    cells.add(j)
                    stack.append(j)
        self._conn[team] = (self.territory_version, cells)
        return cells

    def on_network(self, agent: Agent) -> bool:
        """The HQ, or a squad standing on territory connected to its HQ."""
        return agent.is_hq or (agent.y * self.w + agent.x) in self.connected(agent.team)

    def squad_limit(self, team: int) -> int:
        return max(0, self.tiles[team] - 1)

    def reserved(self, team: int) -> int:
        return sum(o.remaining for o in self.occ.values() if o.team == team)

    def available_gold(self, team: int) -> int:
        return self.gold[team] - self.reserved(team)

    def income(self, team: int) -> int:
        return self.tiles[team] * C.TILE_INCOME + self.rich_tiles[team] * (C.RICH_TILE_INCOME - C.TILE_INCOME)

    def upkeep(self, team: int) -> int:
        return -(-self.soldiers[team] // C.UPKEEP_SOLDIERS_PER_GOLD)

    # ------------------------------------------------------------------ vision

    def blocks(self, team: int, idx: int) -> bool:
        return bool(self.wall[idx]) or self.owner[idx] == 2 - team

    def own_vision(self, team: int, x: int, y: int) -> set:
        """Cells an observer at (x, y) sees: a square of VISION_RADIUS, minus cells hidden
        behind enemy territory or walls."""
        w, h = self.w, self.h
        out = {y * w + x}
        for dx, dy, steps in VISION_OFFSETS:
            tx, ty = x + dx, y + dy
            if not (0 <= tx < w and 0 <= ty < h):
                continue
            ok = True
            for cands in steps:
                if all(not (0 <= x + cx < w and 0 <= y + cy < h) or self.blocks(team, (y + cy) * w + x + cx)
                       for cx, cy in cands):
                    ok = False
                    break
            if ok:
                out.add(ty * w + tx)
        return out

    def agent_vision(self, agent: Agent) -> set:
        own = self.own_vision(agent.team, agent.x, agent.y)
        if self.on_network(agent):
            return own | self.visible[agent.team]
        return own

    # ------------------------------------------------------------------ squads

    def _register(self, team: int, n: int, x: int, y: int) -> Agent:
        s = Agent(self._new_id(), team, x, y, n, False, self.spawned)
        self.spawned += 1
        self.squads[s.id] = s
        self.order.append(s)
        self.squad_count[team] += 1
        return s

    def _remove(self, s: Agent) -> None:
        s.alive = False
        del self.squads[s.id]
        self.squad_count[s.team] -= 1
        self.gone.append(s.id)
        self.changed.discard(s.id)

    def _place(self, s: Agent, idx: int) -> Agent:
        """Put s on idx, merging with a squad of the same team already there.
        The bigger squad survives (ties: the one already there) and keeps its program;
        the other one's memo goes to the survivor's inbox."""
        t = s.team
        other = self.sq[t][idx]
        if other is None or other is s:
            self.sq[t][idx] = s
            s.x, s.y = idx % self.w, idx // self.w
            self.changed.add(s.id)
            return s
        survivor, absorbed = (s, other) if s.n > other.n else (other, s)
        survivor.n += absorbed.n
        survivor.move_left += absorbed.move_left
        if absorbed.memo is not None:
            survivor.inbox.append(absorbed.memo)
        survivor.inbox.extend(absorbed.inbox)
        self.sq[t][idx] = survivor
        survivor.x, survivor.y = idx % self.w, idx // self.w
        self._remove(absorbed)
        self.changed.add(survivor.id)
        return survivor

    def _cancel_occupation(self, idx: int, why: str) -> None:
        o = self.occ.pop(idx)
        self.stats[o.team]["occupations_cancelled"] += 1
        self.occ_ended.append([idx % self.w, idx // self.w, why])

    # ------------------------------------------------------------------ rule checks (None = legal)

    def why_not_move(self, s: Agent, d: Direction, count: int):
        if s.is_hq:
            return "the HQ cannot move"
        if d is Direction.CENTER:
            return "cannot move to Direction.CENTER"
        if count < 1 or count > s.n:
            return f"soldiers must be between 1 and {s.n}"
        if count > s.move_left:
            return f"only {s.move_left} soldiers of this squad can still move this turn"
        x, y = s.x + d.dx, s.y + d.dy
        if not self.in_bounds(x, y):
            return f"({x}, {y}) is off the map"
        idx = y * self.w + x
        if self.wall[idx]:
            return f"({x}, {y}) is a wall"
        if count < s.n and self.sq[s.team][idx] is None and self.squad_count[s.team] >= self.squad_limit(s.team):
            return f"squad limit reached ({self.squad_limit(s.team)}): cannot split off a new squad"
        return None

    def why_not_occupy(self, s: Agent, gold_per_soldier: int):
        if s.is_hq:
            return "the HQ cannot occupy"
        if not 1 <= gold_per_soldier <= C.MAX_GOLD_PER_SOLDIER:
            return f"gold_per_soldier must be between 1 and {C.MAX_GOLD_PER_SOLDIER}"
        idx = s.y * self.w + s.x
        if self.owner[idx] == s.team + 1:
            return "this tile is already yours"
        if self.owner[idx]:
            return "this tile belongs to the enemy (raze it first)"
        if self.wall[idx]:
            return "walls cannot be occupied"
        if idx in self.occ:
            return "this tile is already being occupied"
        if self.sq[1 - s.team][idx] is not None:
            return "an enemy squad is on this tile"
        if self.enemy_adjacent(s.team, idx):
            return "too close to enemy territory (an enemy tile is among the 8 neighbours)"
        total = s.n * gold_per_soldier
        if self.available_gold(s.team) < total:
            return f"not enough gold: needs {total}, available {self.available_gold(s.team)}"
        return None

    def why_not_raze(self, s: Agent):
        if s.is_hq:
            return "the HQ cannot raze"
        idx = s.y * self.w + s.x
        if self.owner[idx] != 2 - s.team:
            return "this tile is not enemy territory"
        if idx == self.hq_idx[1 - s.team]:
            return "an HQ cannot be razed (defeat its defenders instead)"
        if self.sq[1 - s.team][idx] is not None:
            return "enemy soldiers are still on this tile"
        return None

    def why_not_disband(self, s: Agent, count: int):
        if s.is_hq:
            return "the HQ garrison cannot be disbanded"
        if count < 1 or count > s.n:
            return f"soldiers must be between 1 and {s.n}"
        if self.owner[s.y * self.w + s.x] != s.team + 1:
            return "soldiers can only be disbanded on your own territory"
        return None

    def why_not_spawn(self, hq: Agent, x: int, y: int, count: int):
        if not hq.is_hq:
            return "only the HQ can spawn squads"
        if count < 1:
            return "soldiers must be at least 1"
        if self.spawns_left <= 0:
            return f"already spawned {C.MAX_SPAWNS_PER_ROUND} squads this round"
        if not self.in_bounds(x, y):
            return f"({x}, {y}) is off the map"
        idx = y * self.w + x
        if idx not in self.connected(hq.team):
            return f"({x}, {y}) is not territory connected to your HQ"
        cost = count * C.SOLDIER_COST
        if self.available_gold(hq.team) < cost:
            return f"not enough gold: needs {cost}, available {self.available_gold(hq.team)}"
        if self.sq[hq.team][idx] is None and self.squad_count[hq.team] >= self.squad_limit(hq.team):
            return f"squad limit reached ({self.squad_limit(hq.team)})"
        return None

    # ------------------------------------------------------------------ effects (call after the check)

    def do_move(self, s: Agent, d: Direction, count: int) -> Agent:
        w, t = self.w, s.team
        src = s.y * w + s.x
        dst = (s.y + d.dy) * w + (s.x + d.dx)
        if count == s.n:
            mover = s
            self.sq[t][src] = None
            s.move_left -= count
        else:
            s.n -= count
            s.move_left -= count
            self.changed.add(s.id)
            mover = self._register(t, count, s.x, s.y)
            if s.memo is not None:
                mover.inbox.append(s.memo)
        mover.last_move = d
        o = self.occ.get(src)
        if o is not None and o.team == t and self.sq[t][src] is None:
            self._cancel_occupation(src, "left")
        o = self.occ.get(dst)
        if o is not None and o.team != t:
            self._cancel_occupation(dst, "contested")
        self.version += 1
        return self._place(mover, dst)

    def do_occupy(self, s: Agent, gold_per_soldier: int) -> Occupation:
        idx = s.y * self.w + s.x
        total = s.n * gold_per_soldier
        ruined = idx in self.ruins
        o = Occupation(s.team, idx, total, occupation_rounds(total, ruined), ruined, self.turn)
        self.occ[idx] = o
        self.stats[s.team]["occupations_started"] += 1
        self.occ_started.append([s.x, s.y, s.team, o.rounds, total])
        return o

    def do_raze(self, s: Agent) -> None:
        idx = s.y * self.w + s.x
        self._set_owner(idx, 0)
        self.ruins[idx] = C.RUIN_ROUNDS
        self.tile_changes[idx] = RUIN
        self.stats[s.team]["tiles_razed"] += 1
        self.stats[1 - s.team]["tiles_lost"] += 1

    def do_disband(self, s: Agent, count: int) -> None:
        s.n -= count
        s.move_left = min(s.move_left, s.n)
        self.soldiers[s.team] -= count
        if s.n == 0:
            self.sq[s.team][s.y * self.w + s.x] = None
            self._remove(s)
        else:
            self.changed.add(s.id)

    def do_spawn(self, hq: Agent, x: int, y: int, count: int) -> Agent:
        t = hq.team
        cost = count * C.SOLDIER_COST
        self.gold[t] -= cost
        self.spawns_left -= 1
        self.soldiers[t] += count
        st = self.stats[t]
        st["gold_on_soldiers"] += cost
        st["soldiers_spawned"] += count
        idx = y * self.w + x
        existing = self.sq[t][idx]
        self.version += 1
        if existing is not None:  # soldiers join the squad that is already there
            existing.n += count
            self.changed.add(existing.id)
            return existing
        st["squads_spawned"] += 1
        s = self._register(t, count, x, y)
        if hq.memo is not None:
            s.inbox.append(hq.memo)
        return self._place(s, idx)

    # ------------------------------------------------------------------ turn structure

    def _reset_frame(self) -> None:
        self.changed: set = set()
        self.gone: list = []
        self.tile_changes: dict = {}
        self.occ_started: list = []
        self.occ_ended: list = []
        self.fights: list = []
        self.events: list = []
        self.f_ind: list = []
        self.f_dots: list = []
        self.f_lines: list = []
        self.cpu = [0, 0]

    def begin_turn(self) -> int:
        self.turn += 1
        self.round = (self.turn + 1) // 2
        t = self.active = (self.turn - 1) % 2
        self._reset_frame()
        self.spawns_left = C.MAX_SPAWNS_PER_ROUND
        for s in self.order:
            if s.alive and s.team == t:
                s.move_left = s.n
                s.last_move = None
        return t

    def turn_agents(self) -> list:
        """HQ first, then squads in creation order. Squads created during the turn act next turn."""
        t = self.active
        self.order = [s for s in self.order if s.alive]
        return [self.hq[t]] + [s for s in self.order if s.team == t]

    def before_agent_turn(self, a: Agent) -> None:
        a.indicator = a.dots = a.lines = None
        a.inbox_start = len(a.inbox)

    def after_agent_turn(self, a: Agent) -> None:
        a.inbox = a.inbox[a.inbox_start:]  # drop what was readable this turn, keep what arrived during it
        a.inbox_start = 0
        if a.indicator:
            self.f_ind.append([a.id, a.indicator])
        if a.dots and len(self.f_dots) < 400:
            self.f_dots.extend([a.id, *d] for d in a.dots)
        if a.lines and len(self.f_lines) < 400:
            self.f_lines.extend([a.id, *ln] for ln in a.lines)
        self.cpu[0] += a.used
        self.cpu[1] = max(self.cpu[1], a.used)

    def end_turn(self) -> None:
        self._resolve_combat()
        self._check_hq_capture()
        if self.winner is None:
            self._economy(self.active)
            if self.active == 1:
                self._end_round()

    @property
    def over(self) -> bool:
        return self.winner is not None

    def _resolve_combat(self) -> None:
        cells = set()
        for s in self.squads.values():
            idx = s.y * self.w + s.x
            if self.sq[1 - s.team][idx] is not None or (idx == self.hq_idx[1 - s.team] and self.hq[1 - s.team].n > 0):
                cells.add(idx)
        m = C.HQ_DEFENSE_MULTIPLIER
        for idx in sorted(cells):
            side = []
            for t in (0, 1):
                s = self.sq[t][idx]
                g = self.hq[t].n if idx == self.hq_idx[t] else 0
                side.append((s, g, (s.n if s else 0) + g, idx == self.hq_idx[t]))
            if side[0][2] == 0 or side[1][2] == 0:
                continue
            losses = []
            for t in (0, 1):
                _, _, count, defending = side[t]
                _, _, ecount, edef = side[1 - t]
                damage = C.COMBAT_FACTOR * math.sqrt(ecount * (m if edef else 1))
                if defending:
                    damage /= m
                losses.append(min(count, math.ceil(round(damage, 9))))
            for t in (0, 1):
                s, g, _, _ = side[t]
                loss = losses[t]
                st = self.stats[t]
                st["soldiers_lost"] += loss
                self.stats[1 - t]["enemy_soldiers_killed"] += loss
                if s is not None:
                    take = min(loss, s.n)
                    s.n -= take
                    self.soldiers[t] -= take
                    loss -= take
                    s.move_left = min(s.move_left, s.n)
                    if s.n == 0:
                        self.sq[t][idx] = None
                        self._remove(s)
                    else:
                        self.changed.add(s.id)
                if loss:
                    self.hq[t].n -= loss
            self.fights.append([idx % self.w, idx // self.w, losses[0], losses[1]])

    def _check_hq_capture(self) -> None:
        for t in (0, 1):
            idx = self.hq_idx[t]
            if self.sq[1 - t][idx] is not None and self.hq[t].n == 0 and self.sq[t][idx] is None:
                self.winner = 1 - t
                self.reason = f"captured the enemy HQ in round {self.round}"
                return

    def _economy(self, t: int) -> None:
        st = self.stats[t]
        for idx in sorted(i for i, o in self.occ.items() if o.team == t):
            o = self.occ[idx]
            if self.sq[t][idx] is None:
                self._cancel_occupation(idx, "left")
                continue
            amount = o.next_installment()
            self.gold[t] -= amount
            o.paid += amount
            o.ticks += 1
            st["gold_on_occupation"] += amount
            if o.ticks >= o.rounds:
                del self.occ[idx]
                x, y = idx % self.w, idx // self.w
                if self.enemy_adjacent(t, idx):
                    st["occupations_failed"] += 1
                    self.occ_ended.append([x, y, "failed"])
                else:
                    st["tiles_repaired" if o.ruined else "tiles_claimed"] += 1
                    self._set_owner(idx, t + 1)
                    self.occ_ended.append([x, y, "done"])
        inc, up = self.income(t), self.upkeep(t)
        self.gold[t] += inc - up
        st["income_total"] += inc
        st["upkeep_total"] += up

    def _end_round(self) -> None:
        for idx in sorted(self.ruins):
            if idx in self.occ:
                continue  # being repaired: the countdown is paused
            self.ruins[idx] -= 1
            if self.ruins[idx] <= 0:
                del self.ruins[idx]
                self.tile_changes[idx] = BLANK
                self.version += 1
        if self.round >= self.max_rounds:
            self._tiebreak()

    def _tiebreak(self) -> None:
        keys = [
            ("territory", self.tiles[0], self.tiles[1]),
            ("gold", self.gold[0], self.gold[1]),
            ("soldiers", self.soldiers[0] + self.hq[0].n, self.soldiers[1] + self.hq[1].n),
        ]
        for label, a, b in keys:
            if a != b:
                self.winner = 0 if a > b else 1
                self.reason = f"round limit reached; more {label} ({max(a, b)} vs {min(a, b)})"
                return
        self.winner = 1
        self.reason = "round limit reached; territory, gold and soldiers all equal, so the second player wins"

    # ------------------------------------------------------------------ replay

    def note(self, agent: Agent, kind: str) -> None:
        self.events.append([kind, agent.id])

    def initial_frame(self) -> dict:
        w = self.w
        return {
            "tiles": [[i % w, i // w, self.tile_state(i)] for i in range(w * self.h) if self.tile_state(i)],
            "squads": [],
            "gold": list(self.gold),
            "garrison": [self.hq[0].n, self.hq[1].n],
            "hq_ids": [self.hq[0].id, self.hq[1].id],
        }

    def frame(self) -> dict:
        w = self.w
        f: dict = {"team": self.active}
        if self.changed:
            f["sq"] = [[s.id, s.team, s.x, s.y, s.n] for s in sorted((self.squads[i] for i in self.changed if i in self.squads),
                                                                   key=lambda s: s.id)]
        if self.gone:
            f["gone"] = self.gone
        if self.tile_changes:
            f["tiles"] = [[i % w, i // w, v] for i, v in self.tile_changes.items()]
        if self.occ_started:
            f["occ"] = self.occ_started
        if self.occ_ended:
            f["occ_end"] = self.occ_ended
        if self.fights:
            f["fights"] = self.fights
        if self.events:
            f["events"] = self.events
        if self.f_ind:
            f["ind"] = self.f_ind
        if self.f_dots:
            f["dots"] = self.f_dots[:400]
        if self.f_lines:
            f["lines"] = self.f_lines[:400]
        f["gold"] = list(self.gold)
        f["garrison"] = [self.hq[0].n, self.hq[1].n]
        f["terr"] = list(self.tiles)
        f["sold"] = list(self.soldiers)
        f["squads"] = list(self.squad_count)
        f["cpu"] = list(self.cpu)
        return f
