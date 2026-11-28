"""Run real matches and check, after every turn, that the engine's bookkeeping agrees
with the actual state. Random bots exercise odd corners (splits, merges, fights, razes)."""

import pytest

from arena.engine.match import MatchConfig, run_match
from arena.game import constants as C
from arena.game.world import GameWorld


def check(w: GameWorld) -> None:
    n = w.w * w.h
    for t in (0, 1):
        mine = [s for s in w.squads.values() if s.team == t]
        assert len(mine) == w.squad_count[t]
        assert sum(s.n for s in mine) == w.soldiers[t]
        for s in mine:
            assert s.alive and s.n > 0 and 0 <= s.move_left <= s.n
            assert w.sq[t][s.y * w.w + s.x] is s
        on_grid = [s for s in w.sq[t] if s is not None]
        assert len(on_grid) == len(mine)
        owned = {i for i in range(n) if w.owner[i] == t + 1}
        assert owned == w.owned[t] and len(owned) == w.tiles[t]
        assert w.rich_tiles[t] == sum(w.rich[i] for i in owned)
        assert w.owner[w.hq_idx[t]] == t + 1
        assert w.hq[t].n >= 0
        # own and enemy territory never touch, not even diagonally
        for i in owned:
            assert not w.enemy_adjacent(t, i), f"team {t} tile {i} touches enemy territory"
    for i, left in w.ruins.items():
        assert w.owner[i] == 0 and 1 <= left <= C.RUIN_ROUNDS
    for i, o in w.occ.items():
        assert w.owner[i] == 0 and o.idx == i
        assert 0 <= o.ticks < o.rounds and o.paid <= o.total
    if w.turn % 50 == 0:  # territory vision matches a brute-force recomputation
        r = C.VISION_RADIUS
        for t in (0, 1):
            expect = set()
            for i in w.owned[t]:
                x, y = i % w.w, i // w.w
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        if 0 <= x + dx < w.w and 0 <= y + dy < w.h:
                            expect.add((y + dy) * w.w + x + dx)
            assert w.visible[t] == expect


@pytest.mark.parametrize("a,b,seed,rounds", [
    ("bots/randombot", "bots/randombot", 3, 400),
    ("bots/randombot", "bots/randombot", 8, 400),
    ("bots/examplebot", "bots/randombot", 5, 400),
    ("bots/examplebot", "bots/examplebot", 6, 300),
    ("bots/rusher", "bots/examplebot", 2, 300),
])
def test_invariants_hold_every_turn(monkeypatch, a, b, seed, rounds):
    original = GameWorld.end_turn
    turns = []

    def end_turn(self):
        original(self)
        check(self)
        turns.append(self.turn)

    monkeypatch.setattr(GameWorld, "end_turn", end_turn)
    r = run_match(a, b, MatchConfig(seed=seed, max_rounds=rounds))
    assert turns and r["stats"][0]["exceptions"] + r["stats"][1]["exceptions"] <= 1
