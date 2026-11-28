"""Rule-by-rule tests for the territory game, driving GameWorld / RobotController directly."""

import math

import pytest

from arena.engine.meter import StopTurn
from arena.game import constants as C
from arena.game.controller import new_controller, set_active
from arena.game.maps import MapData
from arena.game.types import Direction, GameActionError, Location, TileKind
from arena.game.world import GameWorld

E, N, NE, W = Direction.EAST, Direction.NORTH, Direction.NORTHEAST, Direction.WEST


def make_world(**kw) -> GameWorld:
    m = MapData(20, 20, hq=[(3, 3), (16, 16)], rich={(10, 3)}, symmetry="rotational", name="test")
    w = GameWorld(m, seed=1, **kw)
    w.begin_turn()  # team A to move
    return w


def put(w: GameWorld, team: int, x: int, y: int, n: int):
    s = w._register(team, n, x, y)
    w.soldiers[team] += n
    s.move_left = n
    return w._place(s, y * w.w + x)


def idx(w, x, y):
    return y * w.w + x


def rc_for(w, agent):
    rc = new_controller(w, agent)
    set_active(rc, True)
    return rc


# ----------------------------------------------------------------------- start and economy

def test_start_state():
    w = make_world()
    assert w.tiles == [9, 9] and w.gold == [1000, 1000]
    assert w.hq[0].n == C.HQ_GARRISON and w.squad_limit(0) == 8
    assert w.income(0) == 9


def test_turns_alternate_and_only_the_mover_earns():
    w = make_world()
    assert w.active == 0 and w.round == 1
    w.end_turn()
    assert w.gold == [1009, 1000]
    assert w.begin_turn() == 1 and w.round == 1
    w.end_turn()
    assert w.gold == [1009, 1009]
    assert w.begin_turn() == 0 and w.round == 2


def test_upkeep_rounds_up():
    w = make_world()
    put(w, 0, 5, 5, 11)
    assert w.upkeep(0) == 2


def test_rich_tile_income():
    w = make_world()
    w._set_owner(idx(w, 10, 3), 1)
    assert w.income(0) == 10 * C.TILE_INCOME + (C.RICH_TILE_INCOME - C.TILE_INCOME)


# ----------------------------------------------------------------------- occupation

def test_occupation_formula_and_payments():
    w = make_world()
    s = put(w, 0, 6, 3, 10)
    assert w.why_not_occupy(s, 2) is None
    o = w.do_occupy(s, 2)
    assert o.total == 20 and o.rounds == 10
    gold_before = w.gold[0]
    for r in range(10):
        assert w.tile_state(idx(w, 6, 3)) == 0
        w._economy(0)
    assert w.owner[idx(w, 6, 3)] == 1
    # 10 rounds of income (the new tile counts in the last one) minus upkeep minus 20 gold
    assert gold_before - w.gold[0] == 20 - (9 * 9 + 10) + 10 * w.upkeep(0)


def test_installments_spread_the_remainder_early():
    w = make_world()
    s = put(w, 0, 6, 3, 23)
    o = w.do_occupy(s, 1)
    assert o.rounds == math.ceil(200 / 23) == 9
    paid = []
    while o.ticks < o.rounds:
        paid.append(o.next_installment())
        o.paid += paid[-1]
        o.ticks += 1
    assert paid == [3, 3, 3, 3, 3, 2, 2, 2, 2] and sum(paid) == 23


def test_occupation_caps():
    w = make_world()
    s = put(w, 0, 6, 3, 1)
    assert w.do_occupy(s, 1).rounds == C.CLAIM_MAX_ROUNDS
    big = put(w, 0, 7, 7, 300)
    assert w.do_occupy(big, 1).rounds == 1  # 300 gold, same speed as 200, so it costs more


def test_reserved_gold_blocks_overspending():
    w = make_world()
    w.gold[0] = 30
    s = put(w, 0, 6, 3, 10)
    w.do_occupy(s, 2)
    assert w.available_gold(0) == 10
    assert "not enough gold" in w.why_not_spawn(w.hq[0], 3, 4, 6)
    assert w.why_not_spawn(w.hq[0], 3, 4, 5) is None


def test_negative_gold_blocks_spending_but_not_running_occupations():
    w = make_world()
    s = put(w, 0, 6, 3, 10)
    w.do_occupy(s, 2)
    w.gold[0] = -50
    assert w.why_not_spawn(w.hq[0], 3, 4, 1)
    t = put(w, 0, 8, 8, 1)
    assert w.why_not_occupy(t, 1)
    w._economy(0)
    assert idx(w, 6, 3) in w.occ and w.occ[idx(w, 6, 3)].paid == 2  # still running and still paying


def test_buffer_from_enemy_territory():
    w = make_world()
    w._set_owner(idx(w, 12, 12), 2)
    s = put(w, 0, 11, 11, 5)
    assert "too close" in w.why_not_occupy(s, 1)
    t = put(w, 0, 10, 10, 5)
    assert w.why_not_occupy(t, 1) is None


def test_completion_fails_if_enemy_territory_appeared_next_to_it():
    w = make_world()
    s = put(w, 0, 11, 11, 10)
    w.do_occupy(s, 20 // 10)
    w._set_owner(idx(w, 12, 12), 2)
    for _ in range(10):
        w._economy(0)
    assert w.owner[idx(w, 11, 11)] == 0 and w.stats[0]["occupations_failed"] == 1


def test_leaving_or_enemy_arrival_cancels():
    w = make_world()
    s = put(w, 0, 6, 3, 10)
    w.do_occupy(s, 2)
    w.do_move(s, E, 10)
    assert idx(w, 6, 3) not in w.occ
    t = put(w, 0, 8, 8, 10)
    w.do_occupy(t, 2)
    e = put(w, 1, 9, 9, 3)
    w.do_move(e, Direction.SOUTHWEST, 3)
    assert idx(w, 8, 8) not in w.occ


def test_split_part_keeps_the_occupation_running():
    w = make_world()
    s = put(w, 0, 6, 3, 10)
    w.do_occupy(s, 2)
    w.do_move(s, E, 4)
    assert idx(w, 6, 3) in w.occ and s.n == 6


# ----------------------------------------------------------------------- raze, ruins, repair

def test_raze_ruin_decay_and_repair():
    w = make_world()
    tile = idx(w, 15, 16)  # part of B's camp
    s = put(w, 0, 15, 16, 5)
    assert w.why_not_raze(s) is None
    w.do_raze(s)
    assert w.owner[tile] == 0 and w.ruins[tile] == C.RUIN_ROUNDS and w.tiles[1] == 8
    # repair is cheaper: 10 gold -> 10 rounds (but B's camp is adjacent, so A may not repair here)
    assert "too close" in w.why_not_occupy(s, 2)
    for _ in range(C.RUIN_ROUNDS - 1):
        w._end_round()
    assert w.ruins[tile] == 1
    w._end_round()
    assert tile not in w.ruins and w.tile_state(tile) == 0


def test_repair_pauses_the_countdown():
    w = make_world()
    tile = idx(w, 10, 10)
    w.ruins[tile] = 15
    s = put(w, 0, 10, 10, 5)
    o = w.do_occupy(s, 2)  # 10 gold on a ruin -> 10 rounds
    assert o.rounds == 10 and o.ruined
    w._end_round()
    assert w.ruins[tile] == 15
    w._cancel_occupation(tile, "left")
    w._end_round()
    assert w.ruins[tile] == 14


def test_hq_cannot_be_razed():
    w = make_world()
    s = put(w, 0, 16, 16, 5)
    w.hq[1].n = 0
    assert "HQ" in w.why_not_raze(s)


# ----------------------------------------------------------------------- combat

def test_combat_square_root_losses():
    w = make_world()
    a = put(w, 0, 10, 10, 100)
    b = put(w, 1, 10, 10, 100)
    w._resolve_combat()
    assert a.n == 95 and b.n == 95  # ceil(0.5 * sqrt(100)) = 5


def test_lopsided_combat():
    w = make_world()
    a = put(w, 0, 10, 10, 1000)
    b = put(w, 1, 10, 10, 10)
    w._resolve_combat()
    assert not b.alive and a.n == 1000 - math.ceil(0.5 * math.sqrt(10))


def test_hq_defenders_count_five_times():
    w = make_world()
    a = put(w, 0, 16, 16, 50)
    w._resolve_combat()
    assert a.n == 50 - math.ceil(0.5 * math.sqrt(10 * 5))
    assert w.hq[1].n == 10 - math.ceil(0.5 * math.sqrt(50) / 5)


def test_hq_capture_wins():
    w = make_world()
    w.hq[1].n = 0
    put(w, 0, 16, 16, 1)
    w.end_turn()
    assert w.winner == 0 and "captured" in w.reason


# ----------------------------------------------------------------------- squads

def test_merge_bigger_survives_and_gets_memo():
    w = make_world()
    big = put(w, 0, 10, 10, 20)
    small = put(w, 0, 11, 10, 5)
    small.memo = {"seen": [1, 2]}
    out = w.do_move(small, W, 5)
    assert out is big and big.n == 25 and not small.alive
    assert big.inbox == [{"seen": [1, 2]}]


def test_split_creates_new_squad_with_parent_memo():
    w = make_world()
    s = put(w, 0, 10, 10, 20)
    s.memo = "orders"
    child = w.do_move(s, N, 8)
    assert child is not s and child.n == 8 and s.n == 12 and child.inbox == ["orders"]
    assert child.move_left == 0 and s.move_left == 12


def test_move_capacity_and_squad_limit():
    w = make_world()
    s = put(w, 0, 10, 10, 20)
    w.do_move(s, N, 20)
    assert "can still move" in w.why_not_move(s, N, 1)
    w.squad_count[0] = w.squad_limit(0)
    t = put(w, 0, 5, 10, 4)
    w.squad_count[0] = w.squad_limit(0)
    assert "squad limit" in w.why_not_move(t, N, 2)


def test_spawn_rules():
    w = make_world()
    hq = w.hq[0]
    assert "not territory connected" in w.why_not_spawn(hq, 10, 10, 5)
    for k in range(C.MAX_SPAWNS_PER_ROUND):
        w.do_spawn(hq, 2 + k % 3, 2 + k // 3, 1)
    assert "already spawned" in w.why_not_spawn(hq, 4, 4, 1)
    w.begin_turn()
    w.begin_turn()
    # spawning onto an existing squad adds to it instead of making a new squad
    count = w.squad_count[0]
    w.do_spawn(hq, 2, 2, 3)
    assert w.squad_count[0] == count and w.sq[0][idx(w, 2, 2)].n == 4


def test_disband_only_on_own_territory():
    w = make_world()
    s = put(w, 0, 10, 10, 5)
    assert "own territory" in w.why_not_disband(s, 2)
    t = put(w, 0, 4, 4, 5)
    w.do_disband(t, 5)
    assert not t.alive and w.soldiers[0] == 5


# ----------------------------------------------------------------------- vision and communication

def test_enemy_territory_blocks_sight():
    w = make_world()
    w._set_owner(idx(w, 11, 10), 2)
    seen = w.own_vision(0, 10, 10)
    assert idx(w, 11, 10) in seen          # the blocking tile itself is visible
    assert idx(w, 12, 10) not in seen      # the tile behind it is not
    assert idx(w, 12, 12) in seen          # a diagonal line passes beside it


def test_network_controls_board_and_territory_vision():
    w = make_world()
    far = put(w, 0, 10, 10, 5)
    home = put(w, 0, 4, 4, 5)
    rc_far, rc_home = rc_for(w, far), rc_for(w, home)
    with pytest.raises(GameActionError):
        rc_far.read_board(0)
    rc_home.write_board(3, 42)
    assert rc_home.read_board(3) == 42
    assert rc_home.can_see(Location(1, 1)) and not rc_far.can_see(Location(1, 1))


def test_memo_must_be_plain_data():
    w = make_world()
    rc = rc_for(w, put(w, 0, 10, 10, 5))
    rc.set_memo({"a": [1, 2.5, "x", None, (3, 4)]})
    assert rc.get_memo() == {"a": [1, 2.5, "x", None, (3, 4)]}
    with pytest.raises(TypeError):
        rc.set_memo({"loc": Location(1, 2)})
    with pytest.raises(ValueError):
        rc.set_memo(list(range(C.MEMO_MAX_ITEMS + 5)))


def test_merged_squad_turn_ends_quietly():
    w = make_world()
    big = put(w, 0, 10, 10, 20)
    small = put(w, 0, 11, 10, 5)
    rc = rc_for(w, small)
    rc.move(W)
    with pytest.raises(StopTurn):
        rc.get_soldiers()


def test_sense_tile_reports_kinds():
    w = make_world()
    rc = rc_for(w, w.hq[0])
    t = rc.sense_tile(Location(3, 3))
    assert t.kind == TileKind.TERRITORY and t.hq and t.garrison == C.HQ_GARRISON


def test_tiebreak_order():
    w = make_world(max_rounds=1)
    w.end_turn()
    w.begin_turn()
    w.end_turn()
    assert w.winner == 1 and "second player" in w.reason  # everything equal


def test_ruins_break_connection_but_count_as_a_gap():
    w = make_world()
    for x in (5, 6, 7):
        w._set_owner(idx(w, x, 3), 1)
    assert idx(w, 7, 3) in w.connected(0)
    w._set_owner(idx(w, 5, 3), 0)
    w.ruins[idx(w, 5, 3)] = C.RUIN_ROUNDS
    assert idx(w, 6, 3) not in w.connected(0)                     # cut off from the HQ
    assert "not territory connected" in w.why_not_spawn(w.hq[0], 6, 3, 1)
    assert w.income(0) == w.tiles[0]                                # still produces income
    # a ruin between A and B territory is enough separation
    w._set_owner(idx(w, 9, 3), 2)
    w.ruins[idx(w, 8, 3)] = C.RUIN_ROUNDS
    s = put(w, 0, 7, 4, 3)
    assert w.why_not_occupy(s, 1) is None                           # (8,3) ruin and (9,3) is 2 away
