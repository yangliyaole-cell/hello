import json
import textwrap

from arena.engine.match import MatchConfig, run_match
from arena.engine.tournament import bradley_terry
from arena.game.maps import generate_map
from arena.viewer import render_html


def test_examplebot_beats_baselines():
    for seed in (1, 2):
        for opp in ("bots/randombot", "bots/rusher"):
            r = run_match("bots/examplebot", opp, MatchConfig(seed=seed))
            assert r["result"]["winner_name"] == "examplebot", (seed, opp, r["result"])
            r = run_match(opp, "bots/examplebot", MatchConfig(seed=seed))
            assert r["result"]["winner_name"] == "examplebot", (seed, opp, r["result"])


def test_matches_are_deterministic():
    a = run_match("bots/examplebot", "bots/randombot", MatchConfig(seed=7, max_rounds=200))
    b = run_match("bots/examplebot", "bots/randombot", MatchConfig(seed=7, max_rounds=200))
    assert json.dumps(a["rounds"]) == json.dumps(b["rounds"])
    assert a["result"] == b["result"]


def test_maps_are_symmetric():
    for seed in range(1, 30):
        m = generate_map(seed)
        w, h = m.width, m.height
        mirror = {
            "rotational": lambda x, y: (w - 1 - x, h - 1 - y),
            "mirror_x": lambda x, y: (w - 1 - x, y),
            "mirror_y": lambda x, y: (x, h - 1 - y),
        }[m.symmetry]
        assert {mirror(*p) for p in m.walls} == m.walls
        assert {mirror(*p): a for p, a in m.ore.items()} == m.ore
        assert mirror(*m.hq[0]) == tuple(m.hq[1])
        m.validate()


def test_load_error_forfeits(tmp_path):
    bad = tmp_path / "cheater"
    bad.mkdir()
    (bad / "bot.py").write_text("import os\ndef turn(rc):\n    pass\n")
    r = run_match(str(bad), "bots/randombot", MatchConfig(seed=1))
    assert r["result"]["winner"] == 1
    assert r["load_errors"][0]


def test_runtime_errors_are_reported(tmp_path):
    bot = tmp_path / "buggy"
    bot.mkdir()
    (bot / "bot.py").write_text(textwrap.dedent("""
        from arena.sdk import *
        def turn(rc):
            if rc.get_round() == 3:
                rc.move(Direction.NORTH)   # HQs cannot move -> GameActionError
            if rc.get_round() == 4:
                while True:
                    pass
    """))
    r = run_match(str(bot), "bots/randombot", MatchConfig(seed=1, max_rounds=10))
    kinds = {e["kind"] for e in r["errors"] if e["team"] == 0}
    assert "turn-exception" in kinds and "turn-budget" in kinds
    assert r["stats"][0]["exceptions"] == 1 and r["stats"][0]["budget_exceeded"] == 1


def test_bradley_terry_orders_players():
    ratings = bradley_terry(["a", "b", "c"], [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)] * 3)
    assert ratings["a"] > ratings["b"] > ratings["c"]


def test_viewer_embeds_replay():
    r = run_match("bots/randombot", "bots/randombot", MatchConfig(seed=1, max_rounds=5))
    html = render_html(r)
    assert html.startswith("<!doctype html>") and '"format":"arena-replay"' in html
    assert "</script>" not in html.split('id="replay-data"')[1].split("</script>")[0]
