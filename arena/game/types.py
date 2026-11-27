"""Bot-facing value types.

All of these are immutable, and their classes are sealed (attributes cannot be
reassigned), because both teams share them inside one process: a bot must not be
able to monkeypatch ``Location.add`` and break the opponent.

Coordinates: x grows to the east, y grows to the north. (0, 0) is the south-west corner.
"""

from __future__ import annotations

from operator import itemgetter

from . import constants as C

__all__ = [
    "Direction", "Location", "Team", "RobotType", "Terrain",
    "RobotInfo", "OreInfo", "GameActionError", "GameConstants",
]

_SEALED: set = set()


class _Frozen(type):
    """Metaclass whose classes become read-only once sealed."""

    def __setattr__(cls, name, value):
        if cls in _SEALED:
            raise AttributeError(f"SDK class {cls.__name__} is read-only")
        type.__setattr__(cls, name, value)

    def __delattr__(cls, name):
        if cls in _SEALED:
            raise AttributeError(f"SDK class {cls.__name__} is read-only")
        type.__delattr__(cls, name)


class _Immutable(metaclass=_Frozen):
    __slots__ = ()

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} objects are immutable")

    def __delattr__(self, name):
        raise AttributeError(f"{type(self).__name__} objects are immutable")

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _init_slots(obj, **values):
    for k, v in values.items():
        object.__setattr__(obj, k, v)


# --------------------------------------------------------------------------- Direction

class Direction(_Immutable):
    """One of the 8 compass directions, or CENTER.

    Use the constants ``Direction.NORTH`` ... ``Direction.NORTHWEST`` and ``Direction.CENTER``.
    """

    __slots__ = ("dx", "dy", "name", "index")

    def __init__(self, index: int, name: str, dx: int, dy: int):
        _init_slots(self, index=index, name=name, dx=dx, dy=dy)

    def __repr__(self):
        return f"Direction.{self.name}"

    def __hash__(self):
        return self.index

    def __reduce__(self):
        return (_direction_from_index, (self.index,))

    def rotate_left(self) -> "Direction":
        """45 degrees counter-clockwise. CENTER stays CENTER."""
        return self if self.index == 8 else _DIRS[(self.index + 7) % 8]

    def rotate_right(self) -> "Direction":
        """45 degrees clockwise. CENTER stays CENTER."""
        return self if self.index == 8 else _DIRS[(self.index + 1) % 8]

    def opposite(self) -> "Direction":
        return self if self.index == 8 else _DIRS[(self.index + 4) % 8]

    @staticmethod
    def all() -> tuple:
        """The 8 non-CENTER directions, clockwise starting at NORTH."""
        return _DIRS

    @staticmethod
    def cardinal() -> tuple:
        """NORTH, EAST, SOUTH, WEST."""
        return _CARDINAL

    @staticmethod
    def from_delta(dx: int, dy: int) -> "Direction":
        """Direction for a step (dx, dy) with each component in {-1, 0, 1}."""
        d = _BY_DELTA.get((dx, dy))
        if d is None:
            raise ValueError(f"no direction for delta ({dx}, {dy}); components must be -1, 0 or 1")
        return d


_DIR_SPECS = [
    ("NORTH", 0, 1), ("NORTHEAST", 1, 1), ("EAST", 1, 0), ("SOUTHEAST", 1, -1),
    ("SOUTH", 0, -1), ("SOUTHWEST", -1, -1), ("WEST", -1, 0), ("NORTHWEST", -1, 1),
    ("CENTER", 0, 0),
]
_ALL_WITH_CENTER = tuple(Direction(i, n, dx, dy) for i, (n, dx, dy) in enumerate(_DIR_SPECS))
for _d in _ALL_WITH_CENTER:
    type.__setattr__(Direction, _d.name, _d)
_DIRS = _ALL_WITH_CENTER[:8]
_CARDINAL = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
_BY_DELTA = {(d.dx, d.dy): d for d in _ALL_WITH_CENTER}


def _direction_from_index(i):
    return _ALL_WITH_CENTER[i]


# --------------------------------------------------------------------------- Location

_tuple_new = tuple.__new__


class Location(tuple, metaclass=_Frozen):
    """An immutable (x, y) map coordinate. Behaves like a tuple: ``x, y = loc`` works."""

    __slots__ = ()

    def __new__(cls, x: int, y: int):
        if type(x) is not int or type(y) is not int:
            raise TypeError(f"Location coordinates must be int, got ({type(x).__name__}, {type(y).__name__})")
        return _tuple_new(cls, (x, y))

    x = property(itemgetter(0), doc="x coordinate (grows to the east)")
    y = property(itemgetter(1), doc="y coordinate (grows to the north)")

    def __repr__(self):
        return f"Location({self[0]}, {self[1]})"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return (Location, (self[0], self[1]))

    def add(self, direction: Direction) -> "Location":
        """The neighbouring location in ``direction``."""
        return _tuple_new(Location, (self[0] + direction.dx, self[1] + direction.dy))

    def subtract(self, direction: Direction) -> "Location":
        return _tuple_new(Location, (self[0] - direction.dx, self[1] - direction.dy))

    def translate(self, dx: int, dy: int) -> "Location":
        return Location(self[0] + dx, self[1] + dy)

    def distance_sq(self, other: "Location") -> int:
        """Squared euclidean distance. All game radii are expressed as squared distances."""
        dx = self[0] - other[0]
        dy = self[1] - other[1]
        return dx * dx + dy * dy

    def steps_to(self, other: "Location") -> int:
        """Minimum number of moves between the two locations, ignoring obstacles."""
        return max(abs(self[0] - other[0]), abs(self[1] - other[1]))

    def is_adjacent_to(self, other: "Location") -> bool:
        """True if ``other`` is one of the 8 neighbours (not the same tile)."""
        return 0 < self.distance_sq(other) <= 2

    def direction_to(self, other: "Location") -> Direction:
        """Closest of the 8 directions pointing towards ``other`` (CENTER if equal)."""
        dx = other[0] - self[0]
        dy = other[1] - self[1]
        ax, ay = abs(dx), abs(dy)
        if ax == 0 and ay == 0:
            return Direction.CENTER
        # tan(67.5 deg) ~= 2.414: steeper than that is a straight direction
        sx = (dx > 0) - (dx < 0)
        sy = (dy > 0) - (dy < 0)
        if ax > 2.414 * ay:
            sy = 0
        elif ay > 2.414 * ax:
            sx = 0
        return _BY_DELTA[(sx, sy)]


def _loc(x: int, y: int) -> Location:
    """Engine-side fast constructor (no type checks)."""
    return _tuple_new(Location, (x, y))


# --------------------------------------------------------------------------- Team / Terrain

class Team(_Immutable):
    """Team.A or Team.B."""

    __slots__ = ("name", "index")

    def __init__(self, index: int, name: str):
        _init_slots(self, index=index, name=name)

    def __repr__(self):
        return f"Team.{self.name}"

    def __hash__(self):
        return self.index

    def __reduce__(self):
        return (_team_from_index, (self.index,))

    def opponent(self) -> "Team":
        return _TEAMS[1 - self.index]


_TEAMS = (Team(0, "A"), Team(1, "B"))
type.__setattr__(Team, "A", _TEAMS[0])
type.__setattr__(Team, "B", _TEAMS[1])


def _team_from_index(i):
    return _TEAMS[i]


class Terrain(_Immutable):
    """Terrain.EMPTY (passable) or Terrain.WALL (impassable)."""

    __slots__ = ("name", "index")

    def __init__(self, index: int, name: str):
        _init_slots(self, index=index, name=name)

    def __repr__(self):
        return f"Terrain.{self.name}"

    def __hash__(self):
        return self.index

    def __reduce__(self):
        return (_terrain_from_index, (self.index,))


_TERRAINS = (Terrain(0, "EMPTY"), Terrain(1, "WALL"))
type.__setattr__(Terrain, "EMPTY", _TERRAINS[0])
type.__setattr__(Terrain, "WALL", _TERRAINS[1])


def _terrain_from_index(i):
    return _TERRAINS[i]


# --------------------------------------------------------------------------- RobotType

class RobotType(_Immutable):
    """Robot kinds and their stats: RobotType.HQ, RobotType.MINER, RobotType.SOLDIER."""

    __slots__ = (
        "name", "index", "cost", "health", "vision_radius_sq", "action_radius_sq", "damage",
        "move_cooldown", "action_cooldown", "budget",
    )

    def __init__(self, index: int, name: str, **stats):
        _init_slots(self, index=index, name=name, **stats)

    def __repr__(self):
        return f"RobotType.{self.name}"

    def __hash__(self):
        return self.index

    def __reduce__(self):
        return (_type_from_index, (self.index,))

    @property
    def can_move(self) -> bool:
        return self.move_cooldown > 0

    @property
    def can_attack(self) -> bool:
        return self.damage > 0

    @property
    def can_mine(self) -> bool:
        return self.name == "MINER"

    @property
    def can_build(self) -> bool:
        return self.name == "HQ"

    @staticmethod
    def all() -> tuple:
        return _TYPES

    @staticmethod
    def buildable() -> tuple:
        """Types the HQ can build."""
        return _TYPES[1:]


_TYPES = tuple(RobotType(i, name, **stats) for i, (name, stats) in enumerate(C.ROBOT_STATS.items()))
for _t in _TYPES:
    type.__setattr__(RobotType, _t.name, _t)


def _type_from_index(i):
    return _TYPES[i]


# --------------------------------------------------------------------------- records

class RobotInfo(tuple, metaclass=_Frozen):
    """What you learn about a sensed robot: (id, team, type, location, health)."""

    __slots__ = ()
    id = property(itemgetter(0))
    team = property(itemgetter(1))
    type = property(itemgetter(2))
    location = property(itemgetter(3))
    health = property(itemgetter(4))

    def __new__(cls, id, team, type, location, health):
        return _tuple_new(cls, (id, team, type, location, health))

    def __repr__(self):
        return f"RobotInfo(id={self[0]}, team={self[1]!r}, type={self[2]!r}, location={self[3]!r}, health={self[4]})"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return (RobotInfo, tuple(self))


class OreInfo(tuple, metaclass=_Frozen):
    """A sensed ore deposit: (location, amount)."""

    __slots__ = ()
    location = property(itemgetter(0))
    amount = property(itemgetter(1))

    def __new__(cls, location, amount):
        return _tuple_new(cls, (location, amount))

    def __repr__(self):
        return f"OreInfo(location={self[0]!r}, amount={self[1]})"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return (OreInfo, tuple(self))


class GameActionError(Exception, metaclass=_Frozen):
    """Raised when a bot attempts an illegal action (e.g. moving into a wall).

    Every action has a matching ``can_*`` check that returns False instead of raising.
    """


class GameConstants(metaclass=_Frozen):
    """Read-only game constants."""

    MAX_ROUNDS = C.MAX_ROUNDS
    STARTING_ORE = C.STARTING_ORE
    PASSIVE_INCOME = C.PASSIVE_INCOME
    MINE_AMOUNT = C.MINE_AMOUNT
    COOLDOWN_PER_ROUND = C.COOLDOWN_PER_ROUND
    COOLDOWN_THRESHOLD = C.COOLDOWN_THRESHOLD
    MAX_UNITS_PER_TEAM = C.MAX_UNITS_PER_TEAM
    SHARED_ARRAY_SIZE = C.SHARED_ARRAY_SIZE
    SHARED_ARRAY_MAX_VALUE = C.SHARED_ARRAY_MAX_VALUE
    INIT_BUDGET = C.INIT_BUDGET
    MAP_MIN_SIZE = C.MAP_MIN_SIZE
    MAP_MAX_SIZE = C.MAP_MAX_SIZE


for _cls in (Direction, Location, Team, Terrain, RobotType, RobotInfo, OreInfo, GameActionError,
             GameConstants, _Immutable):
    _SEALED.add(_cls)
