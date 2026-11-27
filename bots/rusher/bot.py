"""rusher: all-in aggression. The HQ builds nothing but soldiers, and every soldier
marches straight at the enemy HQ (guessed from map symmetry)."""

from arena.sdk import *

guess = 0  # per-robot: which symmetry guess this soldier is currently checking


def guesses(rc):
    hq = rc.get_hq_location()
    w, h = rc.get_map_width(), rc.get_map_height()
    return [Location(w - 1 - hq.x, h - 1 - hq.y), Location(w - 1 - hq.x, hq.y), Location(hq.x, h - 1 - hq.y)]


def turn(rc):
    global guess
    enemies = rc.sense_nearby_robots(team=rc.get_team().opponent())
    for e in enemies:
        if rc.can_attack(e.location):
            rc.attack(e.location)
            break

    if rc.get_type() == RobotType.HQ:
        for d in Direction.all():
            if rc.can_build(RobotType.SOLDIER, d):
                rc.build(RobotType.SOLDIER, d)
                break
        return

    me = rc.get_location()
    goal = guesses(rc)[guess]
    if rc.can_sense_location(goal):
        info = rc.sense_robot_at(goal)
        if info is None or info.type != RobotType.HQ:
            guess = (guess + 1) % 3
    hq = [e for e in enemies if e.type == RobotType.HQ]
    if hq:
        goal = hq[0].location
    elif enemies:
        goal = enemies[0].location
    if not rc.is_movement_ready():
        return
    d = me.direction_to(goal)
    for cand in (d, d.rotate_left(), d.rotate_right(), d.rotate_left().rotate_left(), d.rotate_right().rotate_right()):
        if rc.can_move(cand):
            rc.move(cand)
            return
