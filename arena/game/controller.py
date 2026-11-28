"""RobotController: a squad's or HQ's only handle on the game (the "SDK").

A RobotController instance has no attributes at all. Its state lives in a module-level
table bot code cannot reach. Every method:

1. checks that it is being called during this agent's own turn (and that the squad
   still exists; if it merged into a bigger squad, the turn simply ends),
2. charges its instruction cost,
3. validates argument types exactly (bot objects never execute inside the engine),
4. checks the rules and then applies the action.
"""

from __future__ import annotations

import math

from ..engine.meter import METER, StopTurn
from . import constants as C
from .types import (
    Direction, GameActionError, Location, OccupationInfo, SquadInfo, Team, TileInfo, TileKind,
    _Frozen, _loc, _SEALED,
)
from .world import TEAMS, Agent, GameWorld, occupation_rounds

COST = C.API_COST
_charge = METER.charge
_KIND_BY_STATE = (TileKind.BLANK, TileKind.TERRITORY, TileKind.TERRITORY, TileKind.RUINED, TileKind.WALL)

PALETTE = {
    "red": "#e5484d", "green": "#30a46c", "blue": "#0090ff", "yellow": "#ffc53d", "orange": "#f76b15",
    "purple": "#8e4ec6", "cyan": "#00a2c7", "pink": "#d6409f", "white": "#ffffff", "black": "#111111",
    "gray": "#8b8d98",
}


class _Ctx:
    __slots__ = ("world", "agent", "active", "vis_ver", "vis")

    def __init__(self, world: GameWorld, agent: Agent):
        self.world = world
        self.agent = agent
        self.active = False
        self.vis_ver = -1
        self.vis = None


_CTX: dict = {}


def _api(cost: int):
    def deco(fn):
        def method(self, *args, **kwargs):
            ctx = _CTX.get(self)
            if ctx is None or not ctx.active:
                raise GameActionError("a RobotController can only be used during its own turn")
            if not ctx.agent.alive:
                raise StopTurn  # merged into another squad or destroyed: nothing left to control
            _charge(cost)
            return fn(ctx, *args, **kwargs)

        method.__name__ = fn.__name__
        method.__qualname__ = f"RobotController.{fn.__name__}"
        method.__doc__ = fn.__doc__
        return method

    return deco


# --------------------------------------------------------------------------- validation helpers

def _xy(loc) -> tuple[int, int]:
    if type(loc) is not Location:
        raise TypeError(f"expected a Location, got {type(loc).__name__}")
    return loc[0], loc[1]


def _dir(d) -> Direction:
    if type(d) is not Direction:
        raise TypeError(f"expected a Direction, got {type(d).__name__}")
    return d


def _int(v, what: str) -> int:
    if type(v) is not int:
        raise TypeError(f"{what} must be an int, got {type(v).__name__}")
    return v


def _count(a: Agent, soldiers) -> int:
    return a.n if soldiers is None else _int(soldiers, "soldiers")


def _team_filter(team):
    if team is None:
        return None
    if type(team) is not Team:
        raise TypeError(f"team must be a Team or None, got {type(team).__name__}")
    return team.index


def _plain(data, counter: list):
    """Deep-copy plain data (None/bool/int/float/str/list/tuple/dict), enforcing size limits."""
    counter[0] += 1
    if counter[0] > C.MEMO_MAX_ITEMS:
        raise ValueError(f"memo too large (more than {C.MEMO_MAX_ITEMS} values)")
    t = type(data)
    if data is None or t is bool:
        return data
    if t is int:
        if not -(2 ** 63) <= data < 2 ** 63:
            raise ValueError("memo ints must fit in 64 bits")
        return data
    if t is float:
        if not math.isfinite(data):
            raise ValueError("memo floats must be finite")
        return data
    if t is str:
        counter[1] += len(data)
        if counter[1] > C.MEMO_MAX_CHARS:
            raise ValueError(f"memo too large (more than {C.MEMO_MAX_CHARS} characters of strings)")
        return data
    if t is list or t is tuple:
        items = [_plain(v, counter) for v in data]
        return items if t is list else tuple(items)
    if t is dict:
        out = {}
        for k, v in data.items():
            if type(k) is not str and type(k) is not int:
                raise TypeError("memo dict keys must be str or int")
            out[_plain(k, counter)] = _plain(v, counter)
        return out
    raise TypeError(f"memos can only contain None, bool, int, float, str, list, tuple and dict, not {t.__name__}")


def _copy_memo(data) -> tuple:
    counter = [0, 0]
    return _plain(data, counter), counter[0]


def _color(c) -> str:
    if type(c) is str:
        h = PALETTE.get(c)
        if h is None:
            raise ValueError(f"unknown color {c!r}; use one of {', '.join(PALETTE)} or an (r, g, b) tuple")
        return h
    if type(c) is tuple and len(c) == 3 and all(type(v) is int and 0 <= v <= 255 for v in c):
        return "#%02x%02x%02x" % c
    raise TypeError("color must be a palette name or an (r, g, b) tuple of ints 0..255")


def _vision(ctx: _Ctx) -> set:
    w = ctx.world
    if ctx.vis_ver != w.version:
        ctx.vis = w.agent_vision(ctx.agent)
        ctx.vis_ver = w.version
    return ctx.vis


def _visible_idx(ctx: _Ctx, x: int, y: int) -> int:
    w = ctx.world
    if not w.in_bounds(x, y):
        raise GameActionError(f"({x}, {y}) is off the map")
    idx = y * w.w + x
    if idx not in _vision(ctx):
        raise GameActionError(f"cannot see ({x}, {y})")
    return idx


def _tile_info(w: GameWorld, idx: int) -> TileInfo:
    x, y = idx % w.w, idx // w.w
    owner = w.owner[idx]
    hq_team = 0 if idx == w.hq_idx[0] else 1 if idx == w.hq_idx[1] else -1
    o = w.occ.get(idx)
    occ = None if o is None else OccupationInfo(TEAMS[o.team], o.ticks, o.rounds)
    return TileInfo(
        _loc(x, y), _KIND_BY_STATE[w.tile_state(idx)], TEAMS[owner - 1] if owner else None,
        bool(w.rich[idx]), hq_team >= 0, w.hq[hq_team].n if hq_team >= 0 else 0,
        w.ruins.get(idx, 0), occ,
    )


def _squad_info(s: Agent) -> SquadInfo:
    return SquadInfo(s.id, TEAMS[s.team], _loc(s.x, s.y), s.n, s.last_move)


def _require_squad(a: Agent, what: str) -> None:
    if a.is_hq:
        raise GameActionError(f"the HQ cannot {what}")


def _require_network(ctx: _Ctx, what: str) -> None:
    if not ctx.world.on_network(ctx.agent):
        raise GameActionError(f"{what} is only possible from the HQ or territory connected to it")


class RobotController(metaclass=_Frozen):
    """Everything a squad or HQ can know and do. Passed to ``turn(rc)`` every round."""

    __slots__ = ()

    def __repr__(self):
        return "<RobotController>"

    # ------------------------------------------------------------------ self / game info

    @_api(COST["info"])
    def get_id(ctx) -> int:
        return ctx.agent.id

    @_api(COST["info"])
    def get_team(ctx) -> Team:
        return TEAMS[ctx.agent.team]

    @_api(COST["info"])
    def is_hq(ctx) -> bool:
        return ctx.agent.is_hq

    @_api(COST["info"])
    def get_location(ctx) -> Location:
        return _loc(ctx.agent.x, ctx.agent.y)

    @_api(COST["info"])
    def get_soldiers(ctx) -> int:
        """Soldiers in this squad (for the HQ: its garrison)."""
        return ctx.agent.n

    @_api(COST["info"])
    def get_move_capacity(ctx) -> int:
        """How many of this squad's soldiers can still move this turn."""
        return 0 if ctx.agent.is_hq else ctx.agent.move_left

    @_api(COST["info"])
    def get_round(ctx) -> int:
        return ctx.world.round

    @_api(COST["info"])
    def get_turn(ctx) -> int:
        """Global turn number: team A plays odd turns, team B even turns."""
        return ctx.world.turn

    @_api(COST["info"])
    def get_map_width(ctx) -> int:
        return ctx.world.w

    @_api(COST["info"])
    def get_map_height(ctx) -> int:
        return ctx.world.h

    @_api(COST["info"])
    def get_hq_location(ctx) -> Location:
        hq = ctx.world.hq[ctx.agent.team]
        return _loc(hq.x, hq.y)

    @_api(COST["info"])
    def get_gold(ctx) -> int:
        """Your team's gold (can be negative)."""
        return ctx.world.gold[ctx.agent.team]

    @_api(COST["info"])
    def get_available_gold(ctx) -> int:
        """Gold minus what running occupations will still charge. Spending needs this much."""
        return ctx.world.available_gold(ctx.agent.team)

    @_api(COST["info"])
    def get_income(ctx) -> int:
        """Gold your territory produces per round."""
        return ctx.world.income(ctx.agent.team)

    @_api(COST["info"])
    def get_upkeep(ctx) -> int:
        """Gold your soldiers cost per round."""
        return ctx.world.upkeep(ctx.agent.team)

    @_api(COST["info"])
    def get_territory_count(ctx) -> int:
        return ctx.world.tiles[ctx.agent.team]

    @_api(COST["info"])
    def get_total_soldiers(ctx) -> int:
        """Soldiers in all your squads (the HQ garrison not included)."""
        return ctx.world.soldiers[ctx.agent.team]

    @_api(COST["info"])
    def get_squad_count(ctx) -> int:
        return ctx.world.squad_count[ctx.agent.team]

    @_api(COST["info"])
    def get_squad_limit(ctx) -> int:
        """Maximum number of squads: your territory size, not counting the HQ tile."""
        return ctx.world.squad_limit(ctx.agent.team)

    @_api(COST["info"])
    def get_spawns_left(ctx) -> int:
        """Squads the HQ can still spawn this round."""
        return ctx.world.spawns_left if ctx.world.active == ctx.agent.team else 0

    @_api(COST["info"])
    def is_on_network(ctx) -> bool:
        """True for the HQ and for squads standing on territory connected to the HQ."""
        return ctx.world.on_network(ctx.agent)

    @_api(COST["info"])
    def get_budget_left(ctx) -> int:
        return METER.remaining

    @_api(COST["info"])
    def get_budget_used(ctx) -> int:
        return METER.used

    @_api(COST["info"])
    def get_owned_tiles(ctx, connected_only=False) -> list:
        """Locations of all your territory (or only the part connected to your HQ)."""
        w, t = ctx.world, ctx.agent.team
        cells = sorted(w.connected(t) if connected_only else w.owned[t])
        _charge(len(cells) // 4)
        return [_loc(i % w.w, i // w.w) for i in cells]

    # ------------------------------------------------------------------ sensing

    @_api(COST["see"])
    def can_see(ctx, loc) -> bool:
        x, y = _xy(loc)
        w = ctx.world
        return w.in_bounds(x, y) and (y * w.w + x) in _vision(ctx)

    @_api(COST["sense_one"])
    def sense_tile(ctx, loc) -> TileInfo:
        x, y = _xy(loc)
        return _tile_info(ctx.world, _visible_idx(ctx, x, y))

    @_api(COST["sense_one"])
    def sense_squads_at(ctx, loc) -> list:
        """Squads on a visible tile (at most one per team), yourself excluded."""
        x, y = _xy(loc)
        w = ctx.world
        idx = _visible_idx(ctx, x, y)
        return [_squad_info(s) for s in (w.sq[0][idx], w.sq[1][idx]) if s is not None and s is not ctx.agent]

    @_api(COST["sense_nearby"])
    def sense_nearby_squads(ctx, team=None) -> list:
        """Squads you can see with your own eyes (5x5 around you), nearest first."""
        w, a = ctx.world, ctx.agent
        t = _team_filter(team)
        out = []
        for idx in w.own_vision(a.team, a.x, a.y):
            for s in (w.sq[0][idx], w.sq[1][idx]):
                if s is not None and s is not a and (t is None or s.team == t):
                    out.append(s)
        out.sort(key=lambda s: (max(abs(s.x - a.x), abs(s.y - a.y)), s.id))
        _charge(len(out))
        return [_squad_info(s) for s in out]

    @_api(COST["sense_nearby"])
    def sense_nearby_tiles(ctx) -> list:
        """TileInfo for every tile you can see with your own eyes (5x5 around you)."""
        w, a = ctx.world, ctx.agent
        cells = sorted(w.own_vision(a.team, a.x, a.y))
        _charge(len(cells))
        return [_tile_info(w, i) for i in cells]

    @_api(COST["sense_network"])
    def sense_network_squads(ctx, team=None) -> list:
        """Every squad visible to you, including through your territory's vision.
        Only from the HQ or territory connected to it."""
        _require_network(ctx, "sensing through the network")
        w, a = ctx.world, ctx.agent
        t = _team_filter(team)
        vis = _vision(ctx)
        out = [s for s in w.squads.values()
               if s is not a and (t is None or s.team == t) and (s.y * w.w + s.x) in vis]
        out.sort(key=lambda s: s.id)
        _charge(len(out))
        return [_squad_info(s) for s in out]

    @_api(COST["sense_network"])
    def sense_network_tiles(ctx) -> list:
        """TileInfo for every tile visible to you, including through your territory's vision.
        Only from the HQ or territory connected to it."""
        _require_network(ctx, "sensing through the network")
        w = ctx.world
        cells = sorted(_vision(ctx))
        _charge(len(cells))
        return [_tile_info(w, i) for i in cells]

    # ------------------------------------------------------------------ squad actions

    @_api(COST["check"])
    def can_move(ctx, direction, soldiers=None) -> bool:
        d = _dir(direction)
        return ctx.world.why_not_move(ctx.agent, d, _count(ctx.agent, soldiers)) is None

    @_api(COST["action"])
    def move(ctx, direction, soldiers=None) -> int:
        """Move ``soldiers`` (default: the whole squad) one tile. Moving only part of the
        squad splits it: the moving part becomes a new squad (it starts acting next turn).
        Squads of the same team on the same tile merge automatically.
        Returns the id of the squad that now holds the moved soldiers."""
        a = ctx.agent
        d = _dir(direction)
        n = _count(a, soldiers)
        why = ctx.world.why_not_move(a, d, n)
        if why:
            raise GameActionError(f"cannot move {d.name}: {why}")
        return ctx.world.do_move(a, d, n).id

    @_api(COST["info"])
    def occupation_quote(ctx, gold_per_soldier=1) -> tuple:
        """(total_gold, rounds) occupying your current tile would take with this squad."""
        _require_squad(ctx.agent, "occupy")
        g = _int(gold_per_soldier, "gold_per_soldier")
        w, a = ctx.world, ctx.agent
        if not 1 <= g <= C.MAX_GOLD_PER_SOLDIER:
            raise GameActionError(f"gold_per_soldier must be between 1 and {C.MAX_GOLD_PER_SOLDIER}")
        total = a.n * g
        return total, occupation_rounds(total, (a.y * w.w + a.x) in w.ruins)

    @_api(COST["check"])
    def can_occupy(ctx, gold_per_soldier=1) -> bool:
        return ctx.world.why_not_occupy(ctx.agent, _int(gold_per_soldier, "gold_per_soldier")) is None

    @_api(COST["action"])
    def occupy(ctx, gold_per_soldier=1) -> int:
        """Start occupying your blank or ruined tile. Price = soldiers * gold_per_soldier,
        paid in equal parts every round. Returns the number of rounds it will take.
        The squad must stay on the tile until it is done."""
        g = _int(gold_per_soldier, "gold_per_soldier")
        why = ctx.world.why_not_occupy(ctx.agent, g)
        if why:
            raise GameActionError(f"cannot occupy: {why}")
        return ctx.world.do_occupy(ctx.agent, g).rounds

    @_api(COST["check"])
    def can_raze(ctx) -> bool:
        return ctx.world.why_not_raze(ctx.agent) is None

    @_api(COST["action"])
    def raze(ctx) -> None:
        """Turn the enemy tile you stand on (with no enemy soldiers left on it) into a ruin."""
        why = ctx.world.why_not_raze(ctx.agent)
        if why:
            raise GameActionError(f"cannot raze: {why}")
        ctx.world.do_raze(ctx.agent)

    @_api(COST["check"])
    def can_disband(ctx, soldiers=None) -> bool:
        return ctx.world.why_not_disband(ctx.agent, _count(ctx.agent, soldiers)) is None

    @_api(COST["action"])
    def disband(ctx, soldiers=None) -> None:
        """Remove soldiers (default: all) from this squad. Only on your own territory. No refund."""
        n = _count(ctx.agent, soldiers)
        why = ctx.world.why_not_disband(ctx.agent, n)
        if why:
            raise GameActionError(f"cannot disband: {why}")
        ctx.world.do_disband(ctx.agent, n)

    # ------------------------------------------------------------------ HQ actions

    @_api(COST["check"])
    def can_spawn(ctx, location, soldiers) -> bool:
        x, y = _xy(location)
        return ctx.world.why_not_spawn(ctx.agent, x, y, _int(soldiers, "soldiers")) is None

    @_api(COST["action"])
    def spawn(ctx, location, soldiers) -> int:
        """HQ only: buy ``soldiers`` and place them as a new squad on territory connected to
        the HQ. If one of your squads is already there, they join it. Returns the squad id.
        New squads start acting next turn."""
        x, y = _xy(location)
        n = _int(soldiers, "soldiers")
        why = ctx.world.why_not_spawn(ctx.agent, x, y, n)
        if why:
            raise GameActionError(f"cannot spawn at ({x}, {y}): {why}")
        return ctx.world.do_spawn(ctx.agent, x, y, n).id

    # ------------------------------------------------------------------ communication

    @_api(COST["memo"])
    def set_memo(ctx, data) -> None:
        """Store plain data on this squad/HQ. When it merges into another squad, splits off a
        new squad, or (HQ) spawns one, the receiver finds a copy in its inbox."""
        memo, n = _copy_memo(data)
        _charge(n)
        ctx.agent.memo = memo

    @_api(COST["memo"])
    def get_memo(ctx):
        memo, n = _copy_memo(ctx.agent.memo)
        _charge(n)
        return memo

    @_api(COST["memo"])
    def get_inbox(ctx) -> list:
        """Memos received since your last turn (copies). Unread memos are dropped after one turn."""
        a = ctx.agent
        msgs, n = _copy_memo(list(a.inbox))
        _charge(n)
        a.inbox_start = len(a.inbox)  # everything returned now counts as read
        return msgs

    @_api(COST["board_read"])
    def read_board(ctx, index) -> int:
        """Read the team board (128 ints). Only from the HQ or territory connected to it."""
        _require_network(ctx, "reading the board")
        i = _int(index, "index")
        if not 0 <= i < C.BOARD_SIZE:
            raise GameActionError(f"board index {i} out of range 0..{C.BOARD_SIZE - 1}")
        return ctx.world.board[ctx.agent.team][i]

    @_api(COST["board_write"])
    def write_board(ctx, index, value) -> None:
        _require_network(ctx, "writing the board")
        i = _int(index, "index")
        v = _int(value, "value")
        if not 0 <= i < C.BOARD_SIZE:
            raise GameActionError(f"board index {i} out of range 0..{C.BOARD_SIZE - 1}")
        if not C.BOARD_MIN_VALUE <= v <= C.BOARD_MAX_VALUE:
            raise GameActionError("board values must fit in a signed 32-bit int")
        ctx.world.board[ctx.agent.team][i] = v

    # ------------------------------------------------------------------ debug drawing

    @_api(COST["indicator"])
    def set_indicator_string(ctx, text) -> None:
        if type(text) is not str:
            raise TypeError("indicator text must be a str")
        ctx.agent.indicator = text[: C.INDICATOR_STRING_MAX_LEN]

    @_api(COST["indicator"])
    def set_indicator_dot(ctx, loc, color="yellow") -> None:
        x, y = _xy(loc)
        c = _color(color)
        a = ctx.agent
        if a.dots is None:
            a.dots = []
        if len(a.dots) < C.INDICATOR_MAX_DOTS:
            a.dots.append([x, y, c])

    @_api(COST["indicator"])
    def set_indicator_line(ctx, start, end, color="yellow") -> None:
        x1, y1 = _xy(start)
        x2, y2 = _xy(end)
        c = _color(color)
        a = ctx.agent
        if a.lines is None:
            a.lines = []
        if len(a.lines) < C.INDICATOR_MAX_LINES:
            a.lines.append([x1, y1, x2, y2, c])


_SEALED.add(RobotController)


# --------------------------------------------------------------------------- engine-side helpers

def new_controller(world: GameWorld, agent: Agent) -> RobotController:
    rc = object.__new__(RobotController)
    _CTX[rc] = _Ctx(world, agent)
    return rc


def set_active(rc: RobotController, active: bool) -> None:
    _CTX[rc].active = active


def release(rc: RobotController) -> None:
    _CTX.pop(rc, None)
