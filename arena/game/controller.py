"""RobotController: the bot's only handle on the game (the "SDK").

A RobotController instance has no attributes at all. Its state (world, robot) lives
in a module-level table that bot code cannot reach, so the only way to interact with
the game is through the methods below. Every method:

1. checks that it is being called during this robot's own turn,
2. charges its fixed instruction cost (may end the turn if the budget is exhausted),
3. validates argument types exactly (bot objects never execute inside the engine),
4. checks the game rules and then applies the action atomically.
"""

from __future__ import annotations

from ..engine.meter import METER
from . import constants as C
from .types import (
    Direction, GameActionError, Location, OreInfo, RobotInfo, RobotType, Team, Terrain,
    _Frozen, _loc, _SEALED,
)
from .world import TEAMS, GameWorld, Robot

COST = C.API_COST
_charge = METER.charge

PALETTE = {
    "red": "#e5484d", "green": "#30a46c", "blue": "#0090ff", "yellow": "#ffc53d", "orange": "#f76b15",
    "purple": "#8e4ec6", "cyan": "#00a2c7", "pink": "#d6409f", "white": "#ffffff", "black": "#111111",
    "gray": "#8b8d98",
}


class _Ctx:
    __slots__ = ("world", "robot", "active")

    def __init__(self, world: GameWorld, robot: Robot):
        self.world = world
        self.robot = robot
        self.active = False


_CTX: dict = {}


def _api(cost: int):
    def deco(fn):
        def method(self, *args, **kwargs):
            ctx = _CTX.get(self)
            if ctx is None or not ctx.active:
                raise GameActionError("a RobotController can only be used during its own robot's turn")
            _charge(cost)
            return fn(ctx.world, ctx.robot, *args, **kwargs)

        method.__name__ = fn.__name__
        method.__qualname__ = f"RobotController.{fn.__name__}"
        method.__doc__ = fn.__doc__
        return method

    return deco


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


def _radius(r: Robot, radius_sq) -> int:
    vision = r.type.vision_radius_sq
    if radius_sq is None:
        return vision
    radius_sq = _int(radius_sq, "radius_sq")
    return vision if radius_sq < 0 else min(radius_sq, vision)


def _sensable(w: GameWorld, r: Robot, x: int, y: int) -> bool:
    return 0 <= x < w.w and 0 <= y < w.h and (x - r.x) ** 2 + (y - r.y) ** 2 <= r.type.vision_radius_sq


def _require_sensable(w: GameWorld, r: Robot, x: int, y: int) -> None:
    if not _sensable(w, r, x, y):
        raise GameActionError(f"cannot sense ({x}, {y}): off the map or outside vision radius")


def _info(o: Robot) -> RobotInfo:
    return RobotInfo(o.id, TEAMS[o.team], o.type, _loc(o.x, o.y), o.health)


def _color(c) -> str:
    if type(c) is str:
        h = PALETTE.get(c)
        if h is None:
            raise ValueError(f"unknown color {c!r}; use one of {', '.join(PALETTE)} or an (r, g, b) tuple")
        return h
    if type(c) is tuple and len(c) == 3 and all(type(v) is int and 0 <= v <= 255 for v in c):
        return "#%02x%02x%02x" % c
    raise TypeError("color must be a palette name or an (r, g, b) tuple of ints 0..255")


class RobotController(metaclass=_Frozen):
    """Everything a robot can know and do. Passed to ``turn(rc)`` every round."""

    __slots__ = ()

    def __repr__(self):
        return "<RobotController>"

    # ------------------------------------------------------------------ self / game info

    @_api(COST["info"])
    def get_id(w, r) -> int:
        """This robot's unique id."""
        return r.id

    @_api(COST["info"])
    def get_team(w, r) -> Team:
        return TEAMS[r.team]

    @_api(COST["info"])
    def get_type(w, r) -> RobotType:
        return r.type

    @_api(COST["info"])
    def get_location(w, r) -> Location:
        return _loc(r.x, r.y)

    @_api(COST["info"])
    def get_health(w, r) -> int:
        return r.health

    @_api(COST["info"])
    def get_round(w, r) -> int:
        """Current round number, starting at 1."""
        return w.round

    @_api(COST["info"])
    def get_map_width(w, r) -> int:
        return w.w

    @_api(COST["info"])
    def get_map_height(w, r) -> int:
        return w.h

    @_api(COST["info"])
    def get_ore(w, r) -> int:
        """Ore in your team's bank."""
        return w.team_ore[r.team]

    @_api(COST["info"])
    def get_hq_location(w, r) -> Location:
        """Location of your own HQ."""
        hq = w.hq[r.team]
        return _loc(hq.x, hq.y)

    @_api(COST["info"])
    def get_unit_count(w, r) -> int:
        """Number of living non-HQ robots on your team."""
        return w.unit_count[r.team]

    @_api(COST["info"])
    def get_move_cooldown(w, r) -> int:
        return r.move_cd

    @_api(COST["info"])
    def get_action_cooldown(w, r) -> int:
        return r.action_cd

    @_api(COST["info"])
    def is_movement_ready(w, r) -> bool:
        return r.type.can_move and r.move_cd < C.COOLDOWN_THRESHOLD

    @_api(COST["info"])
    def is_action_ready(w, r) -> bool:
        return r.action_cd < C.COOLDOWN_THRESHOLD

    @_api(COST["info"])
    def get_budget_left(w, r) -> int:
        """Instructions left this turn."""
        return METER.remaining

    @_api(COST["info"])
    def get_budget_used(w, r) -> int:
        """Instructions used so far this turn."""
        return METER.used

    # ------------------------------------------------------------------ sensing

    @_api(COST["sense_one"])
    def on_the_map(w, r, loc) -> bool:
        x, y = _xy(loc)
        return w.in_bounds(x, y)

    @_api(COST["sense_one"])
    def can_sense_location(w, r, loc) -> bool:
        x, y = _xy(loc)
        return _sensable(w, r, x, y)

    @_api(COST["sense_many"])
    def sense_nearby_robots(w, r, radius_sq=None, team=None) -> list:
        """Robots within ``radius_sq`` (default/negative: full vision), nearest first.

        ``team``: None for both teams, or a Team. Never includes yourself.
        """
        rad = _radius(r, radius_sq)
        if team is None:
            t = None
        elif type(team) is Team:
            t = team.index
        else:
            raise TypeError(f"team must be a Team or None, got {type(team).__name__}")
        return [_info(o) for o in w.sense_robots(r, rad, t)]

    @_api(COST["sense_one"])
    def sense_robot_at(w, r, loc):
        """RobotInfo of the robot at ``loc`` or None. ``loc`` must be within vision."""
        x, y = _xy(loc)
        _require_sensable(w, r, x, y)
        o = w.robot_at(x, y)
        return None if o is None else _info(o)

    @_api(COST["sense_one"])
    def sense_terrain(w, r, loc) -> Terrain:
        x, y = _xy(loc)
        _require_sensable(w, r, x, y)
        return Terrain.WALL if w.wall[y * w.w + x] else Terrain.EMPTY

    @_api(COST["sense_one"])
    def sense_passable(w, r, loc) -> bool:
        """True if ``loc`` is not a wall (robots standing there are ignored)."""
        x, y = _xy(loc)
        _require_sensable(w, r, x, y)
        return not w.wall[y * w.w + x]

    @_api(COST["sense_one"])
    def sense_ore(w, r, loc) -> int:
        x, y = _xy(loc)
        _require_sensable(w, r, x, y)
        return w.ore[y * w.w + x]

    @_api(COST["sense_many"])
    def sense_nearby_ore(w, r, radius_sq=None) -> list:
        """OreInfo for every tile with ore within ``radius_sq``, nearest first."""
        rad = _radius(r, radius_sq)
        return [OreInfo(_loc(x, y), a) for x, y, a in w.sense_ore(r, rad)]

    # ------------------------------------------------------------------ actions

    @_api(COST["check"])
    def can_move(w, r, direction) -> bool:
        d = _dir(direction)
        return w.why_not_move(r, d.dx, d.dy) is None

    @_api(COST["action"])
    def move(w, r, direction) -> None:
        """Move one tile. Needs movement cooldown < 10; adds the type's move_cooldown."""
        d = _dir(direction)
        why = w.why_not_move(r, d.dx, d.dy)
        if why:
            raise GameActionError(f"cannot move {d.name}: {why}")
        w.do_move(r, d.dx, d.dy)

    @_api(COST["check"])
    def can_attack(w, r, loc) -> bool:
        x, y = _xy(loc)
        return w.why_not_attack(r, x, y) is None

    @_api(COST["action"])
    def attack(w, r, loc) -> None:
        """Damage the enemy robot at ``loc`` (within action_radius_sq)."""
        x, y = _xy(loc)
        why = w.why_not_attack(r, x, y)
        if why:
            raise GameActionError(f"cannot attack ({x}, {y}): {why}")
        w.do_attack(r, x, y)

    @_api(COST["check"])
    def can_mine(w, r, loc) -> bool:
        x, y = _xy(loc)
        return w.why_not_mine(r, x, y) is None

    @_api(COST["action"])
    def mine(w, r, loc) -> None:
        """Miners only: take ore from ``loc`` (your tile or an adjacent one) into the team bank."""
        x, y = _xy(loc)
        why = w.why_not_mine(r, x, y)
        if why:
            raise GameActionError(f"cannot mine ({x}, {y}): {why}")
        w.do_mine(r, x, y)

    @_api(COST["check"])
    def can_build(w, r, robot_type, direction) -> bool:
        if type(robot_type) is not RobotType:
            raise TypeError(f"expected a RobotType, got {type(robot_type).__name__}")
        d = _dir(direction)
        return w.why_not_build(r, robot_type, d.dx, d.dy) is None

    @_api(COST["action"])
    def build(w, r, robot_type, direction) -> int:
        """HQ only: build a robot on the adjacent tile in ``direction``. Returns its id.

        The new robot takes its first turn next round.
        """
        if type(robot_type) is not RobotType:
            raise TypeError(f"expected a RobotType, got {type(robot_type).__name__}")
        d = _dir(direction)
        why = w.why_not_build(r, robot_type, d.dx, d.dy)
        if why:
            raise GameActionError(f"cannot build {robot_type.name} {d.name}: {why}")
        return w.do_build(r, robot_type, d.dx, d.dy).id

    # ------------------------------------------------------------------ communication

    @_api(COST["shared_read"])
    def read_shared(w, r, index) -> int:
        """Read your team's shared array (64 ints, each 0..65535, all start at 0)."""
        i = _int(index, "index")
        if not 0 <= i < C.SHARED_ARRAY_SIZE:
            raise GameActionError(f"shared array index {i} out of range 0..{C.SHARED_ARRAY_SIZE - 1}")
        return w.shared[r.team][i]

    @_api(COST["shared_write"])
    def write_shared(w, r, index, value) -> None:
        i = _int(index, "index")
        v = _int(value, "value")
        if not 0 <= i < C.SHARED_ARRAY_SIZE:
            raise GameActionError(f"shared array index {i} out of range 0..{C.SHARED_ARRAY_SIZE - 1}")
        if not 0 <= v <= C.SHARED_ARRAY_MAX_VALUE:
            raise GameActionError(f"shared array value {v} out of range 0..{C.SHARED_ARRAY_MAX_VALUE}")
        w.shared[r.team][i] = v

    # ------------------------------------------------------------------ debug drawing (shown in the viewer)

    @_api(COST["indicator"])
    def set_indicator_string(w, r, text) -> None:
        if type(text) is not str:
            raise TypeError("indicator text must be a str")
        r.indicator = text[: C.INDICATOR_STRING_MAX_LEN]

    @_api(COST["indicator"])
    def set_indicator_dot(w, r, loc, color="yellow") -> None:
        x, y = _xy(loc)
        c = _color(color)
        if r.dots is None:
            r.dots = []
        if len(r.dots) < C.INDICATOR_MAX_DOTS:
            r.dots.append([x, y, c])

    @_api(COST["indicator"])
    def set_indicator_line(w, r, start, end, color="yellow") -> None:
        x1, y1 = _xy(start)
        x2, y2 = _xy(end)
        c = _color(color)
        if r.lines is None:
            r.lines = []
        if len(r.lines) < C.INDICATOR_MAX_LINES:
            r.lines.append([x1, y1, x2, y2, c])


_SEALED.add(RobotController)


# --------------------------------------------------------------------------- engine-side helpers

def new_controller(world: GameWorld, robot: Robot) -> RobotController:
    rc = object.__new__(RobotController)
    _CTX[rc] = _Ctx(world, robot)
    return rc


def set_active(rc: RobotController, active: bool) -> None:
    _CTX[rc].active = active


def release(rc: RobotController) -> None:
    _CTX.pop(rc, None)
