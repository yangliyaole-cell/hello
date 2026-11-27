"""rusher: all-in aggression. On its first turn the HQ spends almost all its gold on one
huge squad, which marches straight at the enemy HQ (guessed from map symmetry)."""

from arena.sdk import *

guess = 0  # per squad: which symmetry guess it is checking


def guesses(rc):
    hq = rc.get_hq_location()
    w, h = rc.get_map_width(), rc.get_map_height()
    return [Location(w - 1 - hq.x, h - 1 - hq.y), Location(w - 1 - hq.x, hq.y), Location(hq.x, h - 1 - hq.y)]


def turn(rc):
    global guess
    if rc.is_hq():
        gold = rc.get_available_gold()
        if gold >= 100:
            goal = guesses(rc)[0]
            spot = min(rc.get_owned_tiles(connected_only=True), key=lambda t: t.steps_to(goal))
            n = (gold - 20) // GameConstants.SOLDIER_COST
            if rc.can_spawn(spot, n):
                rc.spawn(spot, n)
        return

    me = rc.get_location()
    goal = guesses(rc)[guess]
    if rc.can_see(goal) and not rc.sense_tile(goal).hq:
        guess = (guess + 1) % 3
        goal = guesses(rc)[guess]
    if any(e.location == me for e in rc.sense_nearby_squads(team=rc.get_team().opponent())):
        return  # fighting
    d = me.direction_to(goal)
    for cand in (d, d.rotate_left(), d.rotate_right()):
        if rc.can_move(cand):
            rc.move(cand)
            break
    if rc.can_raze():
        rc.raze()
