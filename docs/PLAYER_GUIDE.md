# Frontier — Player Guide

You are writing a bot for **Frontier**, a turn-based territory game on a 50×50 grid.
Two bots compete to hold the most territory. Your bot is a single Python file,
`bots/<your_name>/bot.py`, that defines one function:

```python
from arena.sdk import *

def turn(rc: RobotController) -> None:
    ...
```

`turn(rc)` is called once per round for your **HQ** and for **each of your squads**.
Every squad and the HQ runs its own copy of your module: module-level variables are that
squad's private memory. How squads can share information is described in section 6.

Rules are enforced by the engine: illegal actions raise `GameActionError`, and compute is
metered (section 7). Every function you can call is documented in [SDK.md](SDK.md).

---

## 1. Time: rounds and turns

- A match lasts at most **2000 rounds**.
- In every round, **team A takes a turn, then team B**. During your turn only your side acts.
- Everything described "per round" happens once per round, at the end of **your own** turn:
  income, upkeep, occupation progress and payments, the spawn limit.
- Ruins (section 4) decay once per round.
- **Fights are resolved at the end of every turn**, yours and the opponent's (so twice per round).

`rc.get_round()` is the round number (1–2000). `rc.get_turn()` is the global turn number:
team A plays odd turns, team B even turns.

## 2. Map and territory

- 50×50 tiles. `x` grows to the **east**, `y` to the **north**, `(0, 0)` is the south-west corner.
- The map is **symmetric**: rotational (`(x, y) -> (49-x, 49-y)`), mirrored left/right
  (`(x, y) -> (49-x, y)`) or mirrored top/bottom (`(x, y) -> (x, 49-y)`). You are not told which.
- Each team has an **HQ** on one tile. At the start, the HQ tile and its 8 neighbours are that
  team's territory (9 tiles).
- A tile is one of: **blank**, **territory** of a team, **ruined** (neutral, see section 4), or a
  wall (no walls exist yet).
- There are 2–3 clusters of 7–8 **resource-rich** tiles on the map.
- Own and enemy territory must always be separated by at least one non-territory tile, including
  diagonally (see occupation rules).

## 3. Economy

- Each team starts with **1000 gold**, an HQ garrison of **10 soldiers**, and no squads.
- **Income** per round: **1 gold per territory tile** (the HQ tile included), **5 gold per
  resource-rich tile** instead of 1. Tiles count whether or not they are connected to the HQ.
- **Upkeep** per round: **1 gold per 10 soldiers** in your squads, rounded up. The HQ garrison is free.
- Gold can become **negative** (upkeep is always paid). While you cannot afford it, you cannot
  spawn soldiers or start occupations. Nothing else changes.
- **Reserved gold**: gold that running occupations will still charge is reserved. New spending
  needs `rc.get_available_gold()` (= gold − reserved) to cover it.

## 4. Squads and actions

A **squad** is a group of soldiers on one tile. All your soldiers on the same tile always form
a single squad: when two of your squads end up on the same tile they **merge** automatically.
The bigger one survives (on a tie, the one that was already there) and keeps its program and
memory; the other one's memo is delivered to the survivor (section 6). If the squad running
the current turn is merged into another squad, its turn ends.

**Spawning (HQ only)**
- `rc.spawn(location, soldiers)` buys soldiers at **2 gold each** and places them as a new squad.
- The location must be your territory **connected to the HQ** (8-neighbour connectivity).
- At most **5 spawns per round**. No limit on soldiers per spawn.
- Your number of squads may not exceed your **territory size minus 1** (the HQ tile does not count):
  8 at the start.
- Spawning onto a tile where you already have a squad adds the soldiers to that squad
  (this does not create a new squad).
- New squads take their first turn next round.

**Moving**
- A squad moves one tile per round in any of the 8 directions: `rc.move(direction, soldiers=None)`.
- Each soldier can move once per round. `rc.get_move_capacity()` says how many of the squad's
  soldiers can still move this turn.
- Moving only some soldiers **splits** the squad: the moving soldiers become a new squad on the
  target tile (it takes its first turn next round). Splitting into a tile without one of your
  squads needs room under the squad limit.
- You may move onto any tile on the map, including enemy territory and tiles with enemy squads.
  Entering a tile that holds an enemy squad starts a fight there; you cannot pass through it.

**Occupying a blank or ruined tile** — `rc.occupy(gold_per_soldier=1)`
- The squad must stand on the tile, with no enemy squad on it, no occupation already running
  there, and **no enemy territory among the 8 neighbours**.
- `gold_per_soldier` is an integer from 1 to 10. **Price = soldiers in the squad × gold_per_soldier.**
- Duration:
  - blank tile: `ceil(200 / price)` rounds, at least 1 and at most 100 (20 gold → 10 rounds);
  - ruined tile: `ceil(100 / price)` rounds, at least 1 and at most 50 (10 gold → 10 rounds).
- The price is paid in equal parts at the end of each of your turns (the remainder of the
  division is paid 1 gold extra in the first rounds). An occupation that has started always
  keeps paying, even if your gold goes negative.
- The tile becomes yours at the end of your turn when the last part is paid, provided no enemy
  territory is adjacent at that moment (otherwise it fails and the gold is spent).
- The occupation is **cancelled** (no further payments) if all your soldiers leave the tile or an
  enemy squad steps onto it. Part of the squad may leave without cancelling it.
- `rc.occupation_quote(gold_per_soldier)` returns `(price, rounds)` for your current tile;
  `occupation_rounds(price, ruined)` from `arena.sdk` computes the same.

**Razing enemy territory** — `rc.raze()`
- Stand on an enemy territory tile with no enemy soldiers on it: it immediately becomes a
  **ruin** (neutral). Free. HQ tiles cannot be razed.
- A ruin is nobody's territory: it produces no income, does not connect territory tiles to
  the HQ (8-neighbour connectivity only passes through territory), and counts as a gap for the
  separation rule (only territory tiles are checked). Either team may occupy (repair) it.
- A ruin turns back into a blank tile after **20 rounds**. While someone is occupying (repairing)
  it the countdown pauses; if that occupation is cancelled, the countdown continues where it stopped.

**Disbanding** — `rc.disband(soldiers=None)`
- Removes soldiers from the squad, only while standing on your own territory. No refund.

**The HQ**
- Never moves. Its garrison of 10 soldiers costs no upkeep and cannot be reinforced (your squads
  can stand on the HQ tile and fight alongside it).
- If an enemy squad is on your HQ tile at the end of a turn and you have no soldiers left there
  (garrison and squad), **you lose immediately**.

## 5. Fights

- A fight happens on a tile that holds soldiers of both teams. At the end of every turn, each
  such tile is resolved once: **both sides lose soldiers at the same time**:

  `loss = ceil(0.5 × sqrt(enemy soldiers on the tile))`

- Defenders on **their own HQ tile** (garrison plus any squad there) count **5×**: the attacker
  takes damage as if they were 5 times as many, and the defenders' losses are divided by 5
  (rounded up). Squads on the HQ tile lose soldiers before the garrison does.
- A squad may leave a fight on its turn. Fights continue every turn while both sides remain.

## 6. Vision and communication

**Vision**
- A squad sees a **5×5 square** around itself (2 tiles in every direction). Your **territory** also
  sees 2 tiles around every territory tile.
- Enemy territory (and walls) blocks sight: a tile is hidden if the line from the viewer to it is
  blocked. The blocking tile itself is visible.
- You always know the locations of all your own territory tiles (`rc.get_owned_tiles()`).
- About visible enemy squads you learn their id, location, soldier count and the direction
  they moved in their last turn (`SquadInfo.last_move`).

**The HQ network**
- The HQ, and any squad standing on your territory **connected to the HQ**, is "on the network"
  (`rc.is_on_network()`). On the network you can:
  - see everything your territory sees (`sense_network_squads`, `sense_network_tiles`);
  - read and write the **team board**: 128 integers (signed 32-bit), all starting at 0.
- Off the network a squad only sees its own 5×5 square and cannot use the board.

**Memos**
- `rc.set_memo(data)` stores plain data on your squad or HQ (`None`, `bool`, `int`, `float`,
  `str`, `list`, `tuple`, `dict` with `str`/`int` keys; at most 256 values and 2000 characters).
- A copy of the memo is delivered to another agent's **inbox** when:
  - your squad merges into a bigger squad (the survivor receives your memo);
  - your squad splits (the new squad receives your memo);
  - the HQ spawns a new squad (the new squad receives the HQ's memo).
- `rc.get_inbox()` returns the memos received since your last turn. Unread memos are dropped
  after one turn.

## 7. Compute budget

- Every Python bytecode instruction your code executes costs **1**. Pure-Python code inside allowed
  library modules (`collections.Counter`, `random`, `copy.deepcopy`, ...) counts too. Each `rc`
  method costs a fixed amount on top (tables below); methods returning many items add 1 per item.
- Budget per turn: **10,000** for a squad, **30,000** for the HQ. Unused budget is lost.
- Module-level code runs once per squad/HQ program, on its first turn, with a separate budget of **50,000**.
- **When the budget runs out the turn ends immediately.** Actions already taken stay taken. Next
  round `turn(rc)` is called again from the start.
- A CPU-time limit of 0.2 s per turn also applies (it matters only for very heavy built-in calls).
- `rc.get_budget_left()` tells you how much is left.

## 8. Sandbox rules

Your file is checked before the match. A bot that fails the checks **forfeits**.

- One file. Allowed imports only: `math`, `random`, `collections` (`deque`, `defaultdict`,
  `Counter`, `OrderedDict`, `namedtuple`), `heapq`, `bisect`, `itertools`, `functools`
  (`lru_cache`, `cache`, `partial`, `reduce`, `cmp_to_key`, `total_ordering`, `wraps`,
  `cached_property`), `copy` (`copy`, `deepcopy`), `dataclasses` (`dataclass`, `field`), `enum`
  (`Enum`, `IntEnum`, `Flag`, `IntFlag`, `auto`), `typing` (common annotation names), `arena.sdk`.
- No names or attributes starting with `__`, except `__init__`, `__name__` and `__post_init__`.
  You may define the usual dunder methods (`__init__`, `__eq__`, `__lt__`, `__hash__`, `__repr__`, ...).
- Not available: `open`, `eval`, `exec`, `compile`, `type`, `id`, `vars`, `globals`, `locals`, `dir`,
  `input`, `format`, `breakpoint`. `getattr`/`setattr`/`hasattr` refuse `__` names.
- `str.format` only on a string literal without `.`/`[` lookups. Use f-strings.
- No `async`, no metaclasses or other class keyword arguments.
- `print()` writes to the match log (up to 1,000 characters per agent per turn) and costs 10.
- `random` is seeded per squad, so matches are reproducible.
- An exception inside `turn` is logged and the agent continues next round. An exception in
  module-level code disables that squad's program for the rest of the match.

## 9. Winning

- Capture the enemy HQ (section 4): immediate win.
- Otherwise, after round 2000: most **territory** tiles wins; if equal, most **gold**; if equal,
  most **soldiers** (squads plus garrison); if still equal, **team B** (the second player) wins.

---

## 10. API reference

The complete reference for every `rc` method, value type and constant, with costs, return
values, error conditions and examples, is in **[SDK.md](SDK.md)**. Read it before writing code.

Quick index:

- About yourself and the game: `get_id`, `get_team`, `is_hq`, `get_location`, `get_soldiers`,
  `get_move_capacity`, `get_round`, `get_turn`, `get_map_width`, `get_map_height`,
  `get_hq_location`, `get_gold`, `get_available_gold`, `get_income`, `get_upkeep`,
  `get_territory_count`, `get_total_soldiers`, `get_squad_count`, `get_squad_limit`,
  `get_spawns_left`, `is_on_network`, `get_budget_left`, `get_budget_used`, `get_owned_tiles`
- Sensing: `can_see`, `sense_tile`, `sense_squads_at`, `sense_nearby_squads`, `sense_nearby_tiles`,
  `sense_network_squads`, `sense_network_tiles`
- Squad actions: `move`, `occupy`, `occupation_quote`, `raze`, `disband` (each with a `can_*` check)
- HQ actions: `spawn` (with `can_spawn`)
- Communication: `set_memo`, `get_memo`, `get_inbox`, `read_board`, `write_board`
- Debug drawing: `set_indicator_string`, `set_indicator_dot`, `set_indicator_line`
- Types: `Location`, `Direction`, `Team`, `TileKind`, `TileInfo`, `SquadInfo`, `OccupationInfo`,
  `GameActionError`, `GameConstants`, `occupation_rounds`

---

## 11. Testing your bot

```bash
python -m arena check bots/<your_name>                         # static checks + short self-play
python -m arena run bots/<your_name> bots/examplebot --seed 3  # one match, prints a report
python -m arena run bots/examplebot bots/<your_name> --seed 3  # same map, other side
python -m arena run bots/<your_name> bots/rusher --logs        # also print your print() output
```

`run` writes `replays/<a>-vs-<b>-s<seed>.json` and an `.html` viewer next to it. The report
lists exceptions (with line numbers), budget overruns and timeouts. Maps change with the seed.
`bots/examplebot/bot.py` is a complete, commented baseline.
