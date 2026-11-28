# Frontier SDK Reference

Everything a bot can use. The rules themselves are in [PLAYER_GUIDE.md](PLAYER_GUIDE.md).

```python
from arena.sdk import *   # RobotController, Location, Direction, Team, TileKind, TileInfo,
                          # SquadInfo, OccupationInfo, GameActionError, GameConstants, occupation_rounds

def turn(rc: RobotController) -> None:
    ...
```

`turn(rc)` runs once per round for the HQ and for every squad. `rc` is that agent's controller.

Conventions used below:

- **Who**: `any` (HQ or squad), `squad` (squads only), `HQ` (HQ only). Calling a squad-only
  method from the HQ (or the reverse) raises `GameActionError`.
- **Cost**: instructions charged on top of your own code. "+n" means one more per returned item.
- **Errors**: every method raises `TypeError` for arguments of the wrong type (for example a
  tuple where a `Location` is expected, or a `float` where an `int` is expected). Illegal actions
  raise `GameActionError` with a message that says why. Each action has a `can_*` twin that
  returns `False` instead of raising.
- If the squad running the turn no longer exists (it merged into a bigger squad), any further
  `rc` call ends the turn quietly.

---

## 1. RobotController

### 1.1 About yourself and the game

| Method | Who | Cost | Returns |
|---|---|---:|---|
| `rc.get_id() -> int` | any | 1 | your squad's / HQ's id (5 digits, random-looking) |
| `rc.get_team() -> Team` | any | 1 | `Team.A` (moves first each round) or `Team.B` |
| `rc.is_hq() -> bool` | any | 1 | `True` for the HQ |
| `rc.get_location() -> Location` | any | 1 | where you are |
| `rc.get_soldiers() -> int` | any | 1 | soldiers in this squad; for the HQ, its garrison |
| `rc.get_move_capacity() -> int` | any | 1 | soldiers of this squad that can still move this turn (HQ: 0) |
| `rc.get_round() -> int` | any | 1 | round number, 1–2000 |
| `rc.get_turn() -> int` | any | 1 | global turn number: A plays odd turns, B even turns |
| `rc.get_map_width() -> int` | any | 1 | 50 |
| `rc.get_map_height() -> int` | any | 1 | 50 |
| `rc.get_hq_location() -> Location` | any | 1 | your own HQ |
| `rc.get_gold() -> int` | any | 1 | your team's gold (may be negative) |
| `rc.get_available_gold() -> int` | any | 1 | gold minus what running occupations will still charge; spending needs this |
| `rc.get_income() -> int` | any | 1 | gold your territory produces per round |
| `rc.get_upkeep() -> int` | any | 1 | gold your squads cost per round |
| `rc.get_territory_count() -> int` | any | 1 | your territory tiles (HQ tile included) |
| `rc.get_total_soldiers() -> int` | any | 1 | soldiers in all your squads (garrison not included) |
| `rc.get_squad_count() -> int` | any | 1 | your squads |
| `rc.get_squad_limit() -> int` | any | 1 | maximum squads: territory tiles − 1 |
| `rc.get_spawns_left() -> int` | any | 1 | spawns your HQ can still make this round |
| `rc.is_on_network() -> bool` | any | 1 | `True` for the HQ and for squads on territory connected to the HQ |
| `rc.get_budget_left() -> int` | any | 1 | instructions left this turn |
| `rc.get_budget_used() -> int` | any | 1 | instructions used this turn |
| `rc.get_owned_tiles(connected_only=False) -> list[Location]` | any | 1 + 1 per 4 tiles | all your territory tiles, or only those connected to your HQ; sorted by y, then x |

### 1.2 Sensing

You see a tile if it is in **your own view** (a 5×5 square around you, minus tiles hidden behind
enemy territory) or, while you are **on the network**, in your **territory's view** (2 tiles around
every territory tile).

| Method | Who | Cost | Returns / notes |
|---|---|---:|---|
| `rc.can_see(loc) -> bool` | any | 2 | whether you can see `loc` now (`False` off the map) |
| `rc.sense_tile(loc) -> TileInfo` | any | 5 | the tile; **raises** `GameActionError` if off the map or not visible |
| `rc.sense_squads_at(loc) -> list[SquadInfo]` | any | 5 | squads on that tile, at most one per team, yourself excluded; **raises** if not visible |
| `rc.sense_nearby_squads(team=None) -> list[SquadInfo]` | any | 20 +n | squads in your own view, nearest first (ties by id), yourself excluded. `team`: `None` for both, or a `Team` |
| `rc.sense_nearby_tiles() -> list[TileInfo]` | any | 20 +n | every tile in your own view (your own tile included), sorted by y, then x |
| `rc.sense_network_squads(team=None) -> list[SquadInfo]` | any, network | 20 +n | every squad you can see including through territory, sorted by id. **Raises** off the network |
| `rc.sense_network_tiles() -> list[TileInfo]` | any, network | 20 +n | every tile you can see including through territory, sorted by y, then x. **Raises** off the network |

### 1.3 Squad actions

| Method | Who | Cost | Effect |
|---|---|---:|---|
| `rc.can_move(direction, soldiers=None) -> bool` | squad | 5 | |
| `rc.move(direction, soldiers=None) -> int` | squad | 10 | moves `soldiers` (default: the whole squad) one tile. Fewer than the whole squad: the moving part becomes a new squad. Returns the id of the squad that holds the moved soldiers afterwards |
| `rc.occupation_quote(gold_per_soldier=1) -> tuple[int, int]` | squad | 1 | `(price, rounds)` for occupying your current tile with this squad |
| `rc.can_occupy(gold_per_soldier=1) -> bool` | squad | 5 | |
| `rc.occupy(gold_per_soldier=1) -> int` | squad | 10 | starts occupying your blank or ruined tile; returns the number of rounds it will take |
| `rc.can_raze() -> bool` | squad | 5 | |
| `rc.raze() -> None` | squad | 10 | turns the enemy territory tile you stand on into a ruin |
| `rc.can_disband(soldiers=None) -> bool` | squad | 5 | |
| `rc.disband(soldiers=None) -> None` | squad | 10 | removes soldiers (default: all) from the squad; only on your own territory; no refund |

`move` fails when: the direction is `CENTER`; `soldiers` is not between 1 and the squad size, or
more than `get_move_capacity()`; the target is off the map or a wall; or the move would create a
new squad (a split onto a tile without one of your squads) while you are at the squad limit.

`occupy` fails when: `gold_per_soldier` is not 1–10; the tile is already territory (yours, or the
enemy's: raze it first) or a wall; an occupation is already running there; an enemy squad is on
the tile; an enemy territory tile is among the 8 neighbours; or `get_available_gold()` is less
than the price.

`raze` fails when: the tile is not enemy territory, is the enemy HQ, or still has enemy soldiers on it.

### 1.4 HQ actions

| Method | Who | Cost | Effect |
|---|---|---:|---|
| `rc.can_spawn(location, soldiers) -> bool` | HQ | 5 | |
| `rc.spawn(location, soldiers) -> int` | HQ | 10 | buys `soldiers` (2 gold each) as a new squad at `location`, or adds them to your squad already there. Returns that squad's id |

`spawn` fails when: `soldiers` < 1; you already spawned 5 times this round; `location` is off the
map or not territory connected to your HQ; `get_available_gold()` is less than the price; or a new
squad is needed while you are at the squad limit.

### 1.5 Communication

| Method | Who | Cost | Notes |
|---|---|---:|---|
| `rc.set_memo(data) -> None` | any | 5 +n | stores a copy of `data` as your memo (`n` = number of values in it) |
| `rc.get_memo() -> object` | any | 5 +n | a copy of your current memo (`None` if never set) |
| `rc.get_inbox() -> list` | any | 5 +n | copies of the memos delivered to you since your last turn |
| `rc.read_board(index) -> int` | any, network | 2 | team board slot `index` (0–127); **raises** off the network |
| `rc.write_board(index, value) -> None` | any, network | 5 | `value` must fit in a signed 32-bit int; **raises** off the network |

Memo data may only contain `None`, `bool`, `int` (64-bit), finite `float`, `str`, `list`, `tuple`
and `dict` (with `str` or `int` keys), nested, with at most 256 values and 2000 string characters
in total. Anything else raises `TypeError` / `ValueError`.

A memo is delivered (as a copy) to: the surviving squad when your squad merges into it; the new
squad when your squad splits; a new squad spawned by the HQ (the HQ's memo). Unread inbox
entries are dropped after one turn.

### 1.6 Debug drawing (shown in the replay viewer, cleared at the start of each of your turns)

| Method | Who | Cost | Notes |
|---|---|---:|---|
| `rc.set_indicator_string(text) -> None` | any | 5 | up to 64 characters |
| `rc.set_indicator_dot(loc, color="yellow") -> None` | any | 5 | up to 16 per turn |
| `rc.set_indicator_line(start, end, color="yellow") -> None` | any | 5 | up to 16 per turn |

`color` is one of `"red"`, `"green"`, `"blue"`, `"yellow"`, `"orange"`, `"purple"`, `"cyan"`,
`"pink"`, `"white"`, `"black"`, `"gray"`, or an `(r, g, b)` tuple of ints 0–255.

`print(...)` also works: the text goes to the match log (up to 1000 characters per agent per
turn, cost 10 per call; `python -m arena run ... --logs` shows it).

---

## 2. Value types

All value types are immutable and can be used as dict keys and in sets.

### `Location(x, y)`

A map coordinate. `x` grows to the east, `y` to the north; `(0, 0)` is the south-west corner.
Behaves like a tuple: `x, y = loc`, `loc == (3, 4)` is `True` for `Location(3, 4)`.
Both coordinates must be `int`.

| Member | Returns |
|---|---|
| `loc.x`, `loc.y` | coordinates |
| `loc.add(direction)` | the neighbouring `Location` in that direction |
| `loc.subtract(direction)` | the neighbouring `Location` in the opposite direction |
| `loc.translate(dx, dy)` | `Location(x + dx, y + dy)` |
| `loc.distance_sq(other)` | squared euclidean distance |
| `loc.steps_to(other)` | moves needed ignoring obstacles: `max(abs(dx), abs(dy))` |
| `loc.is_adjacent_to(other)` | `True` for the 8 neighbours (not the tile itself) |
| `loc.direction_to(other)` | the closest of the 8 directions towards `other` (`CENTER` if equal) |

The constructor does not check bounds; use `rc.can_see` / `rc.sense_tile` or compare with the map size.

### `Direction`

`Direction.NORTH`, `NORTHEAST`, `EAST`, `SOUTHEAST`, `SOUTH`, `SOUTHWEST`, `WEST`, `NORTHWEST`,
and `Direction.CENTER`.

| Member | Returns |
|---|---|
| `d.dx`, `d.dy` | the step (`NORTH` is `(0, 1)`) |
| `d.name` | e.g. `"NORTHEAST"` |
| `d.index` | 0–7 clockwise from `NORTH`, 8 for `CENTER` |
| `d.rotate_left()`, `d.rotate_right()` | 45° counter-clockwise / clockwise (`CENTER` stays `CENTER`) |
| `d.opposite()` | the reverse direction |
| `Direction.all()` | tuple of the 8 non-center directions, clockwise from `NORTH` |
| `Direction.cardinal()` | `(NORTH, EAST, SOUTH, WEST)` |
| `Direction.from_delta(dx, dy)` | the direction for a step with components in {-1, 0, 1} |

### `Team`

`Team.A`, `Team.B`. Members: `.name` (`"A"`/`"B"`), `.index` (0/1), `.opponent()`.

### `TileKind`

`TileKind.BLANK`, `TileKind.TERRITORY`, `TileKind.RUINED`, `TileKind.WALL`. Member: `.name`.

### `TileInfo`

| Field | Meaning |
|---|---|
| `location` | `Location` |
| `kind` | `TileKind` |
| `owner` | `Team` for territory, else `None` |
| `rich` | `True` for a resource-rich tile |
| `hq` | `True` on either team's HQ tile |
| `garrison` | HQ garrison on an HQ tile, else 0 |
| `ruin_rounds` | for a ruin: rounds until it turns blank (paused while being repaired); else 0 |
| `occupation` | `OccupationInfo` if an occupation is running there, else `None` |

### `OccupationInfo`

Fields: `team`, `rounds_done`, `rounds_total`.

### `SquadInfo`

Fields: `id`, `team`, `location`, `soldiers`, `last_move` (the `Direction` it moved in its team's
last turn, or `None`).

### `GameActionError`

Subclass of `Exception`, raised by illegal actions.

---

## 3. Functions and constants

`occupation_rounds(total_gold, ruined=False) -> int`: rounds an occupation takes for a total price
(blank tile: `ceil(200 / price)` in 1–100; ruined tile: `ceil(100 / price)` in 1–50).

`GameConstants` (read-only class attributes):

| Name | Value | Name | Value |
|---|---:|---|---:|
| `MAX_ROUNDS` | 2000 | `MAP_WIDTH`, `MAP_HEIGHT` | 50, 50 |
| `STARTING_GOLD` | 1000 | `SOLDIER_COST` | 2 |
| `TILE_INCOME` | 1 | `RICH_TILE_INCOME` | 5 |
| `UPKEEP_SOLDIERS_PER_GOLD` | 10 | `HQ_GARRISON` | 10 |
| `HQ_DEFENSE_MULTIPLIER` | 5 | `MAX_SPAWNS_PER_ROUND` | 5 |
| `CLAIM_BASE`, `CLAIM_MAX_ROUNDS` | 200, 100 | `REPAIR_BASE`, `REPAIR_MAX_ROUNDS` | 100, 50 |
| `MAX_GOLD_PER_SOLDIER` | 10 | `RUIN_ROUNDS` | 20 |
| `COMBAT_FACTOR` | 0.5 | `VISION_RADIUS` | 2 |
| `BOARD_SIZE` | 128 | `BOARD_MIN_VALUE`, `BOARD_MAX_VALUE` | −2³¹, 2³¹−1 |
| `MEMO_MAX_ITEMS`, `MEMO_MAX_CHARS` | 256, 2000 | `SQUAD_BUDGET`, `HQ_BUDGET`, `INIT_BUDGET` | 10000, 30000, 50000 |

---

## 4. Examples of API usage

Branching on who is running:

```python
def turn(rc):
    if rc.is_hq():
        hq_turn(rc)
    else:
        squad_turn(rc)
```

Spawning on the HQ network:

```python
def hq_turn(rc):
    for loc in rc.get_owned_tiles(connected_only=True):
        if rc.get_spawns_left() == 0:
            break
        if rc.can_spawn(loc, 5):
            new_id = rc.spawn(loc, 5)
```

Moving, splitting and occupying:

```python
def squad_turn(rc):
    if rc.can_occupy(2):
        price, rounds = rc.occupation_quote(2)
        rc.occupy(2)
        rc.set_indicator_string(f"occupying: {price} gold, {rounds} rounds")
        return
    d = Direction.NORTHEAST
    if rc.get_soldiers() > 1 and rc.can_move(d, 1):
        child_id = rc.move(d, 1)            # split one soldier off
    elif rc.can_move(d):
        rc.move(d)                          # move the whole squad
```

Reading what is around you:

```python
me = rc.get_location()
for tile in rc.sense_nearby_tiles():
    if tile.kind == TileKind.BLANK and tile.occupation is None:
        rc.set_indicator_dot(tile.location, "green")
for squad in rc.sense_nearby_squads(team=rc.get_team().opponent()):
    print(squad.id, squad.soldiers, squad.location, squad.last_move)
```

Memos, inbox and the board:

```python
orders = None

def turn(rc):
    global orders
    for memo in rc.get_inbox():
        if isinstance(memo, dict) and "orders" in memo:
            orders = memo["orders"]
    if rc.is_on_network():
        rc.write_board(0, rc.get_round())
        last = rc.read_board(0)
    rc.set_memo({"orders": orders})
```

Catching an illegal action:

```python
try:
    rc.move(Direction.EAST)
except GameActionError as e:
    print("could not move:", e)
```
