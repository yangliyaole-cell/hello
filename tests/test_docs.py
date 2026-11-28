"""Keep the SDK reference in step with the code, and keep its examples runnable."""

import re
import textwrap
from pathlib import Path

import arena.sdk as sdk
from arena.engine.match import MatchConfig, run_match
from arena.engine.sandbox import validate_source
from arena.game import constants as C
from arena.sdk import RobotController

SDK_DOC = (Path(__file__).resolve().parent.parent / "docs" / "SDK.md").read_text(encoding="utf-8")
GUIDE = (Path(__file__).resolve().parent.parent / "docs" / "PLAYER_GUIDE.md").read_text(encoding="utf-8")


def test_every_controller_method_is_documented():
    methods = [n for n in vars(RobotController) if not n.startswith("_")]
    missing = [m for m in methods if f"rc.{m}(" not in SDK_DOC]
    assert not missing, f"undocumented RobotController methods: {missing}"
    documented = set(re.findall(r"rc\.([a-z_]+)\(", SDK_DOC))
    stale = documented - set(methods)
    assert not stale, f"SDK.md mentions methods that do not exist: {stale}"


def test_every_export_is_documented():
    for name in sdk.__all__:
        assert name in SDK_DOC, name


def test_documented_costs_match_the_code():
    for name, cost in [("get_id", C.API_COST["info"]), ("can_see", C.API_COST["see"]),
                       ("sense_tile", C.API_COST["sense_one"]), ("move", C.API_COST["action"]),
                       ("read_board", C.API_COST["board_read"]), ("write_board", C.API_COST["board_write"])]:
        row = next(line for line in SDK_DOC.splitlines() if f"`rc.{name}(" in line and line.startswith("|"))
        assert f"| {cost} |" in row, (name, row)


def test_constants_table_matches():
    for name in ("STARTING_GOLD", "SOLDIER_COST", "HQ_GARRISON", "MAX_SPAWNS_PER_ROUND", "RUIN_ROUNDS",
                 "SQUAD_BUDGET", "HQ_BUDGET", "MAX_ROUNDS"):
        value = getattr(sdk.GameConstants, name)
        row = next(line for line in SDK_DOC.splitlines() if f"`{name}`" in line or f"`{name}," in line or f" `{name}`" in line)
        assert str(value) in row.replace(",", ""), (name, value, row)


def test_examples_pass_the_sandbox_checks(tmp_path):
    blocks = re.findall(r"```python\n(.*?)```", SDK_DOC + GUIDE, re.S)
    assert len(blocks) >= 6
    for block in blocks:
        src = textwrap.dedent(block)
        if "def turn" not in src:
            src += "\ndef turn(rc):\n    pass\n"
        _, errors = validate_source(src)
        assert not errors, (errors, src)


def test_full_examples_run(tmp_path):
    # the snippets from section 4, glued into one bot, must run without errors
    bot = tmp_path / "docbot"
    bot.mkdir()
    (bot / "bot.py").write_text(textwrap.dedent('''
        from arena.sdk import *
        orders = None

        def hq_turn(rc):
            for loc in rc.get_owned_tiles(connected_only=True):
                if rc.get_spawns_left() == 0:
                    break
                if rc.can_spawn(loc, 5):
                    new_id = rc.spawn(loc, 5)

        def squad_turn(rc):
            global orders
            for memo in rc.get_inbox():
                if isinstance(memo, dict) and "orders" in memo:
                    orders = memo["orders"]
            if rc.is_on_network():
                rc.write_board(0, rc.get_round())
            rc.set_memo({"orders": orders})
            for tile in rc.sense_nearby_tiles():
                if tile.kind == TileKind.BLANK and tile.occupation is None:
                    rc.set_indicator_dot(tile.location, "green")
            if rc.can_occupy(2):
                price, rounds = rc.occupation_quote(2)
                rc.occupy(2)
                return
            d = Direction.NORTHEAST
            if rc.get_soldiers() > 1 and rc.can_move(d, 1):
                rc.move(d, 1)
            elif rc.can_move(d):
                rc.move(d)

        def turn(rc):
            if rc.is_hq():
                hq_turn(rc)
            else:
                squad_turn(rc)
    '''))
    r = run_match(str(bot), "bots/randombot", MatchConfig(seed=1, max_rounds=60))
    assert r["stats"][0]["exceptions"] == 0 and r["stats"][0]["budget_exceeded"] == 0, r["errors"][:3]
