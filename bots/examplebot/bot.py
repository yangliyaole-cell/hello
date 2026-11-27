"""examplebot: a reasonable baseline that exercises most of the API.

Strategy
- HQ: shoot enemies in range; otherwise build 4 miners, then mostly soldiers
  (roughly one more miner per two soldiers, up to 12 miners).
- Miner: mine adjacent ore; otherwise walk to the nearest free ore tile; flee soldiers.
- Soldier: hold a rally point until 6 soldiers have been built, then march on the
  enemy HQ. Its location is guessed from map symmetry, guesses proven wrong are
  crossed out in the team's shared array, and the first robot that sees the real HQ
  broadcasts it.
"""

import random

from arena.sdk import *

# Shared array layout (team-wide, 64 slots of 0..65535)
ENEMY_HQ = 0     # encoded location of the enemy HQ, 0 = unknown
RULED_OUT = 1    # bitmask of symmetry guesses that turned out wrong
SOLDIERS = 2     # soldiers built so far (written by the HQ)

RANK = {RobotType.SOLDIER: 0, RobotType.MINER: 1, RobotType.HQ: 2}

# Per-robot memory. Every robot gets its own copy of these module globals.
built = {"MINER": 0, "SOLDIER": 0}
recent = []
explore_target = None


def encode(loc):
    return loc.x * 128 + loc.y + 1


def decode(v):
    v -= 1
    return Location(v // 128, v % 128)


def fan(d):
    """d first, then directions further and further away from d."""
    out = [d]
    left = right = d
    for _ in range(3):
        left = left.rotate_left()
        right = right.rotate_right()
        out.append(left)
        out.append(right)
    out.append(d.opposite())
    return out


def move_toward(rc, target):
    """Greedy step towards target that avoids tiles visited recently (crude wall following)."""
    if not rc.is_movement_ready():
        return False
    me = rc.get_location()
    if me == target:
        return False
    options = fan(me.direction_to(target))[:7]
    for d in options:
        if me.add(d) not in recent and rc.can_move(d):
            rc.move(d)
            recent.append(me)
            if len(recent) > 12:
                recent.pop(0)
            return True
    recent.clear()
    for d in options:
        if rc.can_move(d):
            rc.move(d)
            return True
    return False


def best_target(rc, enemies):
    """Location of the best enemy to shoot right now, or None."""
    in_range = [e for e in enemies if rc.can_attack(e.location)]
    if not in_range:
        return None
    return min(in_range, key=lambda e: (RANK[e.type], e.health)).location


def note_enemy_hq(rc, enemies):
    for e in enemies:
        if e.type == RobotType.HQ:
            rc.write_shared(ENEMY_HQ, encode(e.location))
            return


def enemy_hq_target(rc):
    v = rc.read_shared(ENEMY_HQ)
    if v:
        return decode(v)
    hq = rc.get_hq_location()
    w, h = rc.get_map_width(), rc.get_map_height()
    guesses = [
        Location(w - 1 - hq.x, h - 1 - hq.y),  # rotational symmetry
        Location(w - 1 - hq.x, hq.y),          # mirrored left/right
        Location(hq.x, h - 1 - hq.y),          # mirrored top/bottom
    ]
    ruled = rc.read_shared(RULED_OUT)
    for i, g in enumerate(guesses):
        if ruled & (1 << i):
            continue
        if rc.can_sense_location(g):
            info = rc.sense_robot_at(g)
            if info is None or info.type != RobotType.HQ or info.team == rc.get_team():
                ruled |= 1 << i
                rc.write_shared(RULED_OUT, ruled)
                continue
        return g
    return guesses[0]


def explore(rc):
    global explore_target
    me = rc.get_location()
    if explore_target is None or me.distance_sq(explore_target) <= 2 or rc.get_round() % 40 == 0:
        explore_target = Location(random.randrange(rc.get_map_width()), random.randrange(rc.get_map_height()))
    rc.set_indicator_line(me, explore_target, "gray")
    move_toward(rc, explore_target)


# --------------------------------------------------------------------------- HQ

def hq_turn(rc):
    enemies = rc.sense_nearby_robots(team=rc.get_team().opponent())
    target = best_target(rc, enemies)
    if target is not None:
        rc.attack(target)
        rc.set_indicator_string("defending")
        return
    if not rc.is_action_ready():
        return

    miners, soldiers = built["MINER"], built["SOLDIER"]
    threatened = any(e.type == RobotType.SOLDIER for e in enemies)
    if threatened:
        kind = RobotType.SOLDIER
    elif miners < 4:
        kind = RobotType.MINER
    elif miners < 12 and soldiers >= 2 * (miners - 3):
        kind = RobotType.MINER
    else:
        kind = RobotType.SOLDIER
    if rc.get_ore() < kind.cost:
        rc.set_indicator_string(f"saving for {kind.name}")
        return

    me = rc.get_location()
    ores = rc.sense_nearby_ore()
    if kind == RobotType.MINER and ores:
        goal = ores[0].location
    else:
        goal = enemy_hq_target(rc)
    for d in fan(me.direction_to(goal)):
        if rc.can_build(kind, d):
            rc.build(kind, d)
            built[kind.name] += 1
            rc.write_shared(SOLDIERS, built["SOLDIER"])
            rc.set_indicator_string(f"built {kind.name} #{built[kind.name]}")
            return


# --------------------------------------------------------------------------- miner

def miner_turn(rc):
    me = rc.get_location()
    enemies = rc.sense_nearby_robots(team=rc.get_team().opponent())
    note_enemy_hq(rc, enemies)

    threats = [e for e in enemies if e.type == RobotType.SOLDIER]
    if threats and rc.is_movement_ready():
        away = threats[0].location.direction_to(me)
        for d in fan(away)[:3]:
            if rc.can_move(d):
                rc.move(d)
                me = rc.get_location()
                break

    ores = rc.sense_nearby_ore()
    for o in ores:
        if me.distance_sq(o.location) <= 2 and rc.can_mine(o.location):
            rc.mine(o.location)
            rc.set_indicator_dot(o.location, "yellow")
            break

    if not rc.is_movement_ready():
        return
    if any(me.distance_sq(o.location) <= 2 and o.amount > 0 for o in rc.sense_nearby_ore(2)):
        return  # still ore next to us: stay
    for o in ores:
        if me.distance_sq(o.location) > 2 and rc.sense_robot_at(o.location) is None:
            rc.set_indicator_line(me, o.location, "yellow")
            move_toward(rc, o.location)
            return
    explore(rc)


# --------------------------------------------------------------------------- soldier

def soldier_turn(rc):
    me = rc.get_location()
    enemies = rc.sense_nearby_robots(team=rc.get_team().opponent())
    note_enemy_hq(rc, enemies)

    target = best_target(rc, enemies)
    if target is not None:
        rc.attack(target)
        rc.set_indicator_line(me, target, "red")
    if enemies:
        if target is None:
            move_toward(rc, enemies[0].location)
        return

    goal = enemy_hq_target(rc)
    if rc.read_shared(SOLDIERS) < 6 and rc.get_round() < 250:
        hq = rc.get_hq_location()
        rally = Location((hq.x * 3 + goal.x) // 4, (hq.y * 3 + goal.y) // 4)
        rc.set_indicator_string("rallying")
        if me.distance_sq(rally) > 4:
            move_toward(rc, rally)
        return
    rc.set_indicator_string("attacking")
    rc.set_indicator_line(me, goal, "red")
    move_toward(rc, goal)


def turn(rc):
    t = rc.get_type()
    if t == RobotType.HQ:
        hq_turn(rc)
    elif t == RobotType.MINER:
        miner_turn(rc)
    else:
        soldier_turn(rc)
