"""examplebot: a reasonable baseline for Frontier that exercises most of the API.

HQ
- Watches everything its territory can see (sense_network_squads).
- Enemies close to the HQ: spawns defenders onto the HQ tile, where they fight 5x stronger.
- Small enemy groups elsewhere: spawns a hunter squad next to them.
- Otherwise spawns small expansion squads on the outer edge of its territory, and from
  round 150 on, occasionally a large attack squad aimed at the enemy HQ.
- Tells each new squad its job through its memo (the squad reads it from its inbox).

Squads
- expand: occupy the current tile if possible, else walk to the best visible blank tile
  (resource-rich and next to our territory preferred); avoid bigger enemies.
- attack: march on the enemy HQ (guessed from map symmetry), razing enemy tiles on the way.
- hunt: chase the enemies it was sent after, then become an expander.
"""

import random

from arena.sdk import *

# Per-agent memory: every squad (and the HQ) has its own copy of these globals.
role = None
target = None
guess_index = 0
explore_target = None
explore_until = 0
attack_ready_round = 150


def enemy_hq_guesses(rc):
    hq = rc.get_hq_location()
    w, h = rc.get_map_width(), rc.get_map_height()
    return [
        Location(w - 1 - hq.x, h - 1 - hq.y),  # rotational symmetry
        Location(w - 1 - hq.x, hq.y),          # mirrored left/right
        Location(hq.x, h - 1 - hq.y),          # mirrored top/bottom
    ]


def step_toward(rc, goal, avoid=None):
    """One step towards goal, trying the direct direction first, then neighbours."""
    me = rc.get_location()
    if me == goal:
        return False
    d = me.direction_to(goal)
    for cand in (d, d.rotate_left(), d.rotate_right(), d.rotate_left().rotate_left(), d.rotate_right().rotate_right()):
        nxt = me.add(cand)
        if avoid and nxt in avoid:
            continue
        if rc.can_move(cand):
            rc.move(cand)
            return True
    return False


def step_away(rc, threat):
    me = rc.get_location()
    away = threat.direction_to(me)
    if away == Direction.CENTER:
        away = me.direction_to(rc.get_hq_location())
    for cand in (away, away.rotate_left(), away.rotate_right()):
        if rc.can_move(cand):
            rc.move(cand)
            return True
    return False


# --------------------------------------------------------------------------- HQ

def nearest(tiles, goal, sample=40):
    pool = tiles if len(tiles) <= sample else random.sample(tiles, sample)
    return min(pool, key=lambda t: t.steps_to(goal))


def spawn_with_role(rc, loc, soldiers, memo):
    rc.set_memo(memo)
    if rc.can_spawn(loc, soldiers):
        rc.spawn(loc, soldiers)
        return True
    return False


def hq_turn(rc):
    global attack_ready_round
    me = rc.get_location()
    team = rc.get_team()
    enemies = rc.sense_network_squads(team=team.opponent())
    connected = rc.get_owned_tiles(connected_only=True)

    # 1. defend the HQ
    threat = sum(e.soldiers for e in enemies if e.location.steps_to(me) <= 5)
    if threat:
        defenders = rc.get_soldiers() + sum(s.soldiers for s in rc.sense_squads_at(me) if s.team == team)
        need = threat // 4 + 10 - defenders
        affordable = max(0, rc.get_available_gold()) // GameConstants.SOLDIER_COST
        if need > 0 and affordable > 0:
            spawn_with_role(rc, me, min(need, affordable), {"role": "guard"})
        rc.set_indicator_string(f"threat {threat}")

    # 2. hunt small raiders elsewhere (look at a few of them at most: budget)
    raiders = [e for e in enemies if e.soldiers <= 40 and e.location.steps_to(me) > 5]
    for e in raiders[:4]:
        if rc.get_spawns_left() == 0:
            break
        if rc.get_available_gold() < 300 + 2 * (e.soldiers * 2 + 2):
            break
        loc = nearest(connected, e.location)
        if loc.steps_to(e.location) <= 4:
            spawn_with_role(rc, loc, e.soldiers * 2 + 2, {"role": "hunt", "target": [e.location.x, e.location.y]})
            break

    # Keep enough gold to put ~150 defenders on the HQ tile at short notice.
    reserve = 300 + threat
    # 3. attack squad, once the economy can carry it
    if (rc.get_round() >= attack_ready_round and rc.get_available_gold() > 400 + reserve
            and rc.get_income() > rc.get_upkeep() + 15):
        size = min(250, (rc.get_available_gold() - reserve) // 2)
        loc = nearest(connected, enemy_hq_guesses(rc)[0])
        if spawn_with_role(rc, loc, size, {"role": "attack"}):
            attack_ready_round = rc.get_round() + 100
            rc.set_indicator_string(f"attack squad of {size}")

    # 4. expansion: small squads on different outer tiles, while upkeep stays affordable
    if rc.get_upkeep() + 1 > rc.get_income() + 5:
        return
    occupied = {s.location for s in rc.sense_network_squads(team=team)}
    spawned = 0
    while (spawned < 3 and rc.get_spawns_left() > 0 and rc.get_squad_count() < rc.get_squad_limit()
           and rc.get_available_gold() >= reserve + 12):
        pool = connected if len(connected) <= 24 else random.sample(connected, 24)
        pool = [t for t in pool if t not in occupied and t != me]
        if not pool:
            break
        loc = max(pool, key=lambda t: t.steps_to(me) + random.random())  # outer edge
        if not spawn_with_role(rc, loc, 6, {"role": "expand"}):
            break
        occupied.add(loc)
        spawned += 1


# --------------------------------------------------------------------------- squads

def read_orders(rc):
    global role, target
    for m in rc.get_inbox():
        if isinstance(m, dict) and "role" in m:
            role = m["role"]
            t = m.get("target")
            target = Location(t[0], t[1]) if t else None
    if role is None:
        role = "expand"
    rc.set_memo({"role": role})  # squads split from us inherit our job


def explore(rc):
    global explore_target, explore_until
    me = rc.get_location()
    if explore_target is None or me == explore_target or rc.get_round() > explore_until:
        hq = rc.get_hq_location()
        dx, dy = me.x - hq.x, me.y - hq.y
        w, h = rc.get_map_width(), rc.get_map_height()
        x = min(w - 1, max(0, me.x + dx + random.randint(-6, 6)))
        y = min(h - 1, max(0, me.y + dy + random.randint(-6, 6)))
        explore_target = Location(x, y)
        explore_until = rc.get_round() + 25
    rc.set_indicator_line(me, explore_target, "gray")
    step_toward(rc, explore_target)


def expand_turn(rc, me, team, enemies):
    n = rc.get_soldiers()
    for e in enemies:
        if e.soldiers >= n and e.location.steps_to(me) <= 2:
            step_away(rc, e.location)
            return

    here = rc.sense_tile(me)
    if here.occupation is not None and here.occupation.team == team:
        rc.set_indicator_string(f"occupying {here.occupation.rounds_done}/{here.occupation.rounds_total}")
        return
    if rc.can_occupy(1):
        g = min(GameConstants.MAX_GOLD_PER_SOLDIER, max(1, -(-40 // n)))  # aim for ~40 gold, 5 rounds
        while g > 1 and not rc.can_occupy(g):
            g -= 1
        rc.occupy(g)
        return

    tiles = rc.sense_nearby_tiles()
    owners = {t.location: t.owner for t in tiles}
    taken = {s.location for s in rc.sense_nearby_squads(team=team)}
    enemy = team.opponent()
    best, best_score = None, None
    for t in tiles:
        if t.occupation is not None or t.location in taken:
            continue
        if t.kind != TileKind.BLANK and t.kind != TileKind.RUINED:
            continue
        loc = t.location
        near_own = near_enemy = False
        for d in Direction.all():
            o = owners.get(loc.add(d))
            if o == team:
                near_own = True
            elif o == enemy:
                near_enemy = True
        if near_enemy:
            continue
        score = loc.steps_to(me) - 2 * near_own - 6 * t.rich + random.random()
        if best_score is None or score < best_score:
            best, best_score = loc, score
    if best is not None:
        rc.set_indicator_line(me, best, "green")
        step_toward(rc, best)
    else:
        explore(rc)


def attack_turn(rc, me, team, enemies):
    global guess_index, target
    if target is None:
        guesses = enemy_hq_guesses(rc)
        goal = guesses[guess_index % 3]
        if rc.can_see(goal):
            info = rc.sense_tile(goal)
            if info.hq and info.owner == team.opponent():
                target = goal
            else:
                guess_index += 1
                goal = guesses[guess_index % 3]
    goal = target or enemy_hq_guesses(rc)[guess_index % 3]
    n = rc.get_soldiers()
    weak = [e for e in enemies if e.soldiers * 2 < n and e.location.steps_to(me) == 1]
    if weak:
        goal = weak[0].location
    rc.set_indicator_line(me, goal, "red")
    step_toward(rc, goal)
    if rc.can_raze():
        rc.raze()


def hunt_turn(rc, me, team, enemies):
    global role, target
    n = rc.get_soldiers()
    prey = [e for e in enemies if e.soldiers <= n]
    if prey:
        step_toward(rc, prey[0].location)
        return
    if target is None or me.steps_to(target) <= 1:
        role = "expand"
        rc.set_memo({"role": role})
        return
    step_toward(rc, target)


quiet_rounds = 0


def guard_turn(rc, me, enemies):
    """Stay on the HQ tile; once the threat has been gone for a while, cut upkeep."""
    global quiet_rounds
    quiet_rounds = 0 if enemies else quiet_rounds + 1
    if quiet_rounds >= 30 and rc.get_soldiers() > 20 and rc.can_disband(rc.get_soldiers() - 20):
        rc.disband(rc.get_soldiers() - 20)


def squad_turn(rc):
    if role is None:
        read_orders(rc)
    me = rc.get_location()
    team = rc.get_team()
    enemies = rc.sense_nearby_squads(team=team.opponent())
    if rc.can_raze():
        rc.raze()
    if any(e.location == me for e in enemies):
        return  # in a fight: hold the tile
    if role == "guard":
        guard_turn(rc, me, enemies)
        return
    if role == "attack":
        attack_turn(rc, me, team, enemies)
    elif role == "hunt":
        hunt_turn(rc, me, team, enemies)
    else:
        expand_turn(rc, me, team, enemies)


def turn(rc):
    if rc.is_hq():
        hq_turn(rc)
    else:
        squad_turn(rc)
