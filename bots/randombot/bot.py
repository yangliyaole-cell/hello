"""randombot: the weakest possible baseline. Moves, builds and attacks at random."""

import random

from arena.sdk import *


def turn(rc):
    t = rc.get_type()
    if t == RobotType.HQ:
        enemies = rc.sense_nearby_robots(team=rc.get_team().opponent())
        targets = [e for e in enemies if rc.can_attack(e.location)]
        if targets:
            rc.attack(random.choice(targets).location)
            return
        kind = random.choice(RobotType.buildable())
        d = random.choice(Direction.all())
        if rc.can_build(kind, d):
            rc.build(kind, d)
        return

    if t == RobotType.MINER:
        for ore in rc.sense_nearby_ore(2):
            if rc.can_mine(ore.location):
                rc.mine(ore.location)
                break
    elif t == RobotType.SOLDIER:
        for e in rc.sense_nearby_robots(team=rc.get_team().opponent()):
            if rc.can_attack(e.location):
                rc.attack(e.location)
                break

    d = random.choice(Direction.all())
    if rc.can_move(d):
        rc.move(d)
