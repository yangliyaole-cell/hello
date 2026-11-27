"""randombot: the weakest possible baseline. Spawns, wanders, occupies and razes at random."""

import random

from arena.sdk import *


def turn(rc):
    if rc.is_hq():
        tiles = rc.get_owned_tiles(connected_only=True)
        for _ in range(rc.get_spawns_left()):
            loc = random.choice(tiles)
            n = random.randint(1, 20)
            if rc.can_spawn(loc, n):
                rc.spawn(loc, n)
        return

    here = rc.sense_tile(rc.get_location())
    if rc.can_raze():
        rc.raze()
    if here.occupation is not None and here.occupation.team == rc.get_team():
        return  # stay until our occupation finishes
    g = random.randint(1, 3)
    if random.random() < 0.4 and rc.can_occupy(g):
        rc.occupy(g)
        return
    d = random.choice(Direction.all())
    n = rc.get_soldiers()
    if n > 1 and random.random() < 0.15:
        n = random.randint(1, n - 1)  # split off part of the squad
    if rc.can_move(d, n):
        rc.move(d, n)
