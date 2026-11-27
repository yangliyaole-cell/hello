# Ore Rush — Player Guide (v0)

You are writing a bot for **Ore Rush**, a Battlecode-style programming game. Two bots
fight on a grid map. Your bot is a single Python file, `bots/<your_name>/bot.py`, that
defines one function:

```python
from arena.sdk import *

def turn(rc: RobotController) -> None:
    ...
```

`turn(rc)` is called once per round **for every robot you own**. Each robot runs its own
copy of your module, so module-level variables are that robot's private memory. Robots
of the same team can only talk through the team's shared array.

Everything you need is in this document. Rules are enforced by the engine: illegal
actions raise `GameActionError`, compute is metered, and the sandbox blocks access to
anything outside the API.

---

## 1. The game

### Map
- A rectangular grid, 24–36 tiles per side. `x` grows to the **east**, `y` grows to the
  **north**, `(0, 0)` is the south-west corner.
- Tiles are either passable or walls. Some passable tiles hold an **ore deposit**
  (40–120 ore each when the match starts; deposits shrink as they are mined).
- Maps are always **symmetric**: rotational (`(x, y) -> (W-1-x, H-1-y)`), mirrored
  left/right (`(x, y) -> (W-1-x, y)`) or mirrored top/bottom (`(x, y) -> (x, H-1-y)`).
  You are not told which. Your HQ's mirror image under one of these is the enemy HQ.

### Teams and robots
Each team starts with one **HQ** and 150 ore, and receives **+2 ore every round**.

| Type      | Cost | Health | Vision r² | Action r² | Damage | Move cooldown | Action cooldown | Budget / turn |
|-----------|-----:|-------:|----------:|----------:|-------:|--------------:|----------------:|--------------:|
| `HQ`      |   –  |  1000  |    34     |    13     |    4   | cannot move   |       10        |    20,000     |
| `MINER`   |  40  |    30  |    20     |     2     |    –   |      10       |       10        |    10,000     |
| `SOLDIER` |  60  |    50  |    20     |    10     |    6   |      10       |       10        |    10,000     |

All radii are **squared** euclidean distances (`dx*dx + dy*dy`). A radius² of 2 means
"your tile or one of the 8 neighbours".

- **HQ**: cannot move. Builds units on an adjacent tile and can shoot nearby enemies.
- **MINER**: mines ore from its own tile or an adjacent tile, `+2` ore to the team bank per action. Cannot attack.
- **SOLDIER**: attacks enemy robots within action radius² 10.
- A team can have at most **50 living units** (HQ not counted).

### Rounds and turns
- At most **1000 rounds**. Each round every robot takes one turn, one robot at a time,
  in spawn order (team A's HQ, team B's HQ, then units in the order they were built).
  A robot sees the world as it is at the moment its turn runs.
- A robot built this round takes its first turn next round.
- **Cooldowns**: moving adds the type's move cooldown, acting (attack/mine/build) adds the
  action cooldown. At the start of every round both cooldowns drop by 10. A robot may
  move while its move cooldown is `< 10`, and act while its action cooldown is `< 10`.
  With the numbers above, every unit can **move once and act once per round**.
- Move = one step in any of the 8 directions onto a passable, empty, on-map tile.

### Winning
- Destroy the enemy HQ: you win immediately.
- If round 1000 ends first, the winner is decided by, in order: HQ health, army value
  (sum of the costs of living units), ore in the bank, total ore mined, then a seeded coin flip.

### What you know
- Always: the map size, your own HQ's location, your team's ore, the round number, your own state.
- Only within your vision radius²: robots, terrain and ore. Sensing outside it raises `GameActionError`.

---

## 2. Compute budget

This is a core part of the game, as in Battlecode.

- Every Python bytecode instruction your code executes costs **1**. Pure-Python code in
  allowed library modules (`collections.Counter`, `random`, `copy.deepcopy`, ...) counts too.
- Every `rc` method call costs a fixed amount on top (see the API tables below).
- Budget per turn: **20,000** for the HQ, **10,000** for units. Unused budget does not carry over.
- Module-level code (imports, constant tables) runs once per robot, the first time that
  robot takes a turn, with a separate budget of **50,000**.
- **When the budget runs out, the turn ends immediately.** Actions already taken stay
  taken. Next round `turn(rc)` is called again from the top. You cannot catch this.
- There is also a CPU-time limit of 0.1 s per turn, as a backstop for very heavy built-in
  calls (for example sorting millions of items). Normal code never gets near it.
- `rc.get_budget_left()` tells you what you have left. Use it to stop expensive work early.

Practical numbers: a simple turn costs 200–500 instructions; one `sense_nearby_robots` is
100 plus whatever you do with the result. A full BFS over a 36×36 map costs roughly
30–60k instructions, which does **not** fit in one turn. Spread work over several turns or
use cheaper navigation.

---

## 3. Sandbox rules

Your file is checked before the match. A bot that fails the checks **forfeits**.

- One file. Allowed imports only:
  `math`, `random`, `collections` (`deque`, `defaultdict`, `Counter`, `OrderedDict`,
  `namedtuple`), `heapq`, `bisect`, `itertools`, `functools` (`lru_cache`, `cache`,
  `partial`, `reduce`, `cmp_to_key`, `total_ordering`, `wraps`, `cached_property`), `copy`
  (`copy`, `deepcopy`), `dataclasses` (`dataclass`, `field`), `enum` (`Enum`, `IntEnum`,
  `Flag`, `IntFlag`, `auto`), `typing` (common annotation names), and `arena.sdk`.
- No names or attributes starting with `__`, except `__init__`, `__name__` and
  `__post_init__`. You may **define** the usual dunder methods (`__init__`, `__eq__`,
  `__lt__`, `__hash__`, `__repr__`, `__iter__`, `__len__`, `__getitem__`, ...).
- Not available: `open`, `eval`, `exec`, `compile`, `type`, `id`, `vars`, `globals`,
  `locals`, `dir`, `input`, `format`, `breakpoint`. `getattr`/`setattr`/`hasattr` work but
  refuse `__` names.
- `str.format` only works on a string literal without `.`/`[` lookups. Use f-strings.
- No `async`, no metaclasses or other class keyword arguments.
- `print()` writes to the match log (up to 2,000 characters per robot per turn) and costs 10.
- `random` is seeded per robot, so matches are reproducible.
- An exception inside `turn` is logged and the robot simply continues next round. An
  exception in module-level code disables that robot for the rest of the match.
- Do not try to get around the sandbox or the budget. It will not work, and every attempt
  shows up in the match report.

---

## 4. API reference

### RobotController (`rc`)

Wrong argument types raise `TypeError`. Illegal actions raise `GameActionError` with a
message saying why. Every action has a `can_*` check that returns `False` instead.

**Information** (cost 1 each)

| Method | Returns |
|---|---|
| `get_id()` | your robot id (random 4-digit int) |
| `get_team()` | `Team.A` or `Team.B` |
| `get_type()` | `RobotType` |
| `get_location()` | `Location` |
| `get_health()` | int |
| `get_round()` | current round, starting at 1 |
| `get_map_width()`, `get_map_height()` | int |
| `get_ore()` | ore in your team's bank |
| `get_hq_location()` | your own HQ's `Location` |
| `get_unit_count()` | your living non-HQ robots |
| `get_move_cooldown()`, `get_action_cooldown()` | int |
| `is_movement_ready()`, `is_action_ready()` | bool |
| `get_budget_left()`, `get_budget_used()` | instructions this turn |

**Sensing**

| Method | Cost | Notes |
|---|---:|---|
| `on_the_map(loc)` | 5 | bool |
| `can_sense_location(loc)` | 5 | on the map and within your vision radius² |
| `sense_nearby_robots(radius_sq=None, team=None)` | 100 | list of `RobotInfo`, nearest first, never includes you. `radius_sq=None` or negative means full vision. `team`: `None` (both) or a `Team` |
| `sense_robot_at(loc)` | 5 | `RobotInfo` or `None` |
| `sense_terrain(loc)` | 5 | `Terrain.EMPTY` or `Terrain.WALL` |
| `sense_passable(loc)` | 5 | `True` if not a wall (robots ignored) |
| `sense_ore(loc)` | 5 | ore amount on that tile |
| `sense_nearby_ore(radius_sq=None)` | 100 | list of `OreInfo` for tiles with ore, nearest first |

The single-tile methods raise `GameActionError` if `loc` is outside your vision.
Ties in distance come back in an arbitrary (but fixed per match) order.

**Actions**

| Method | Cost | Rules |
|---|---:|---|
| `can_move(direction)` / `move(direction)` | 5 / 10 | movable type, move cooldown < 10, target on map, not a wall, empty, not `CENTER` |
| `can_attack(loc)` / `attack(loc)` | 5 / 10 | type with damage, action cooldown < 10, `loc` within action radius², an **enemy** robot is there |
| `can_mine(loc)` / `mine(loc)` | 5 / 10 | `MINER`, action cooldown < 10, `loc` within radius² 2, ore > 0 |
| `can_build(robot_type, direction)` / `build(robot_type, direction)` | 5 / 10 | `HQ`, `MINER` or `SOLDIER`, action cooldown < 10, enough ore, under the unit limit, adjacent tile on map, not a wall, empty. `build` returns the new robot's id |

**Team communication**

| Method | Cost | Notes |
|---|---:|---|
| `read_shared(index)` | 2 | team-wide array of 64 ints, all start at 0 |
| `write_shared(index, value)` | 5 | `0 <= index < 64`, `0 <= value <= 65535` |

**Debug drawing** (shown in the replay viewer; reset at the start of each of your turns)

| Method | Cost | Notes |
|---|---:|---|
| `set_indicator_string(text)` | 5 | up to 64 characters, shown when the robot is selected |
| `set_indicator_dot(loc, color="yellow")` | 5 | up to 16 per turn |
| `set_indicator_line(start, end, color="yellow")` | 5 | up to 16 per turn |

Colors: `"red"`, `"green"`, `"blue"`, `"yellow"`, `"orange"`, `"purple"`, `"cyan"`,
`"pink"`, `"white"`, `"black"`, `"gray"`, or an `(r, g, b)` tuple of ints 0–255.

### Value types (from `arena.sdk`, all immutable)

**`Location(x, y)`** — behaves like a tuple (`x, y = loc`, usable as a dict key).
- `.x`, `.y`
- `.add(direction)`, `.subtract(direction)`, `.translate(dx, dy)` → `Location`
- `.distance_sq(other)` → squared euclidean distance
- `.steps_to(other)` → moves needed ignoring obstacles (`max(|dx|, |dy|)`)
- `.is_adjacent_to(other)` → one of the 8 neighbours
- `.direction_to(other)` → nearest of the 8 directions (`CENTER` if equal)

**`Direction`** — `NORTH`, `NORTHEAST`, `EAST`, `SOUTHEAST`, `SOUTH`, `SOUTHWEST`, `WEST`, `NORTHWEST`, `CENTER`.
- `.dx`, `.dy`, `.name`
- `.rotate_left()`, `.rotate_right()` (45°), `.opposite()`
- `Direction.all()` → the 8 non-center directions, clockwise from `NORTH`
- `Direction.cardinal()` → `NORTH, EAST, SOUTH, WEST`
- `Direction.from_delta(dx, dy)` with components in {-1, 0, 1}

**`Team`** — `Team.A`, `Team.B`; `.opponent()`, `.name`.

**`RobotType`** — `RobotType.HQ`, `RobotType.MINER`, `RobotType.SOLDIER`.
- `.name`, `.cost`, `.health`, `.vision_radius_sq`, `.action_radius_sq`, `.damage`,
  `.move_cooldown`, `.action_cooldown`, `.budget`
- `.can_move`, `.can_attack`, `.can_mine`, `.can_build` (properties)
- `RobotType.all()`, `RobotType.buildable()` (`MINER`, `SOLDIER`)

**`Terrain`** — `Terrain.EMPTY`, `Terrain.WALL`.

**`RobotInfo`** — `.id`, `.team`, `.type`, `.location`, `.health`.

**`OreInfo`** — `.location`, `.amount`.

**`GameActionError`** — raised by illegal actions.

**`GameConstants`** — `MAX_ROUNDS`, `STARTING_ORE`, `PASSIVE_INCOME`, `MINE_AMOUNT`,
`COOLDOWN_PER_ROUND`, `COOLDOWN_THRESHOLD`, `MAX_UNITS_PER_TEAM`, `SHARED_ARRAY_SIZE`,
`SHARED_ARRAY_MAX_VALUE`, `INIT_BUDGET`, `MAP_MIN_SIZE`, `MAP_MAX_SIZE`.

---

## 5. Minimal bot

```python
from arena.sdk import *

def turn(rc):
    me = rc.get_type()
    enemy = rc.get_team().opponent()

    if me == RobotType.HQ:
        for d in Direction.all():
            if rc.can_build(RobotType.MINER, d):
                rc.build(RobotType.MINER, d)
                return
        return

    if me == RobotType.MINER:
        for ore in rc.sense_nearby_ore():
            if rc.can_mine(ore.location):
                rc.mine(ore.location)
                return
            # walk towards the nearest ore
            d = rc.get_location().direction_to(ore.location)
            if rc.can_move(d):
                rc.move(d)
            return
```

`bots/examplebot/bot.py` is a complete, commented baseline (economy, symmetry-based
enemy-HQ search, shared-array communication, debug drawing).

---

## 6. Testing your bot

```bash
python -m arena check bots/<your_name>                         # static checks + 300-round self-play
python -m arena run bots/<your_name> bots/examplebot --seed 3  # one match, prints a report
python -m arena run bots/examplebot bots/<your_name> --seed 3  # same map, other side
python -m arena run bots/<your_name> bots/rusher --logs        # also print your print() output
```

`run` writes `replays/<a>-vs-<b>-s<seed>.json` and an `.html` viewer next to it. The
report lists exceptions (with line numbers), budget overruns and timeouts. Try several
seeds: maps, symmetry and HQ positions change with the seed.
