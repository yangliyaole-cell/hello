"""The sandbox must stop the shortcuts an AI might try: reading engine state,
monkeypatching shared classes, unlimited compute, and cross-robot memory."""

import textwrap

import pytest

from arena.engine.loader import BotProgram
from arena.engine.match import MatchConfig, run_match
from arena.engine.meter import BUDGET, ERROR, METER, OK, TIMEOUT
from arena.engine.sandbox import make_globals, validate_source


def errors_for(src: str) -> list[str]:
    _, errs = validate_source(textwrap.dedent(src) + "\ndef turn(rc):\n    pass\n")
    return errs


def exec_bot(src: str, budget: int = 50_000, time_limit: float = 1.0):
    """Execute bot module code under the meter. Returns (status, globals, exc)."""
    prog = BotProgram.from_source("t", textwrap.dedent(src) + "\ndef turn(rc):\n    pass\n")
    assert prog.ok, prog.errors
    logs = []
    g = make_globals("bot_test", "seed", logs.append, 10)
    status, used, exc = METER.run(exec, (prog.code, g), budget, time_limit)
    return status, g, exc


# ----------------------------------------------------------------------- static checks

@pytest.mark.parametrize("src", [
    "x = ().__class__.__base__.__subclasses__()",
    "import os",
    "import sys",
    "from arena.game import world",
    "from . import helper",
    "x = '{0.__class__}'.format(1)",
    "f = 'abc'.format_map",
    "def g():\n    yield 1\nfr = g().gi_frame",
    "async def f():\n    pass",
    "class M:\n    def __getattr__(self, n):\n        return 1",
    "class X(metaclass=type):\n    pass",
    "__builtins__['open']",
    "x = int.mro()",
])
def test_static_rejections(src):
    assert errors_for(src), f"should have been rejected: {src!r}"


@pytest.mark.parametrize("src", [
    "from arena.sdk import *",
    "import arena.sdk as sdk",
    "import math, heapq, collections, itertools, functools, random",
    "from collections import deque, Counter",
    "from dataclasses import dataclass",
    "from typing import List, Optional",
    "x = 'a{}b{0}'.format(1)",
    "class P:\n    def __init__(self):\n        self._private = 1\n    def __lt__(self, o):\n        return True",
    "if __name__ == 'x':\n    pass",
    "try:\n    pass\nexcept:\n    pass\nfinally:\n    pass",
])
def test_static_allowed(src):
    assert errors_for(src) == []


def test_turn_function_required():
    _, errs = validate_source("x = 1\n")
    assert any("turn(rc)" in e for e in errs)


# ----------------------------------------------------------------------- runtime checks

def test_budget_stops_infinite_loop():
    status, _, _ = exec_bot("while True:\n    pass\n", budget=10_000)
    assert status == BUDGET


@pytest.mark.parametrize("handler", [
    "except Exception:\n            pass",
    "except:\n            pass",
    "except NotDefinedAnywhere:\n            pass",
    "finally:\n            while True:\n                pass",
])
def test_budget_cannot_be_swallowed(handler):
    src = f"""
    while True:
        try:
            while True:
                pass
        {handler}
    """
    status, _, _ = exec_bot(src, budget=10_000)
    assert status == BUDGET


def test_identical_sources_are_both_metered():
    # Code objects compare by value; a mirror match must still meter the second team.
    src = "while True:\n    pass\ndef turn(rc):\n    pass\n"
    progs = [BotProgram.from_source(n, src) for n in ("a", "b")]
    assert progs[0].code == progs[1].code and progs[0].code is not progs[1].code
    for prog in progs:
        g = make_globals("bot_test", "seed", lambda s: None, 10)
        assert METER.run(exec, (prog.code, g), 10_000, 1.0)[0] == BUDGET


def test_mirror_match_meters_both_sides():
    r = run_match("bots/examplebot", "bots/examplebot", MatchConfig(seed=1, max_rounds=150))
    a, b = (st["avg_instructions_per_turn"] for st in r["stats"])
    assert 0.7 < a / b < 1.4, (a, b)


def test_cpu_time_limit_catches_c_level_work():
    # sorted() runs in C: a handful of instructions, lots of CPU.
    status, _, _ = exec_bot("while True:\n    x = sorted(range(3_000_000), reverse=True)\n", budget=10**9, time_limit=0.05)
    assert status == TIMEOUT


def test_library_python_code_is_metered():
    # Counter is implemented in Python, so building one costs instructions.
    status, _, _ = exec_bot("from collections import Counter\nfor _ in range(1000):\n    Counter('abcdefgh' * 50)\n", budget=20_000)
    assert status == BUDGET


@pytest.mark.parametrize("src", [
    "getattr(1, '__class__')",
    "open('/etc/passwd')",
    "eval('1')",
    "type(1)",
    "vars()",
    "globals()",
])
def test_runtime_escapes_fail(src):
    status, _, exc = exec_bot(src)
    assert status == ERROR and isinstance(exc, (AttributeError, NameError)), (status, exc)


@pytest.mark.parametrize("src", [
    "from arena.sdk import Location\nLocation.add = None",
    "from arena.sdk import Direction\nDirection.NORTH = Direction.SOUTH",
    "from arena.sdk import RobotController\nRobotController.move = None",
    "from arena.sdk import Location\nLocation(1, 2).x = 5",
])
def test_sdk_is_read_only(src):
    status, _, exc = exec_bot(src)
    assert status == ERROR and isinstance(exc, AttributeError), (status, exc)


def test_stdlib_patches_stay_private():
    # Patching a whitelisted class only affects this robot's private copy.
    status, _, _ = exec_bot("from collections import Counter\nCounter.most_common = None\nimport copy\ncopy.deepcopy.stash = 1")
    assert status == OK
    import collections
    import copy
    assert callable(collections.Counter.most_common)
    assert not hasattr(copy.deepcopy, "stash")


def test_controller_has_no_reachable_state():
    status, _, exc = exec_bot("from arena.sdk import RobotController\nrc = RobotController\nx = rc._CTX")
    assert status == ERROR and isinstance(exc, AttributeError)


def test_squads_have_private_globals(tmp_path):
    """Squads of the same team must not share module globals."""
    bot = tmp_path / "counter"
    bot.mkdir()
    (bot / "bot.py").write_text(textwrap.dedent("""
        from arena.sdk import *
        count = 0
        first = None
        def turn(rc):
            global count, first
            if rc.is_hq():
                if rc.get_round() < 4:
                    for loc in rc.get_owned_tiles(connected_only=True)[:3]:
                        if rc.can_spawn(loc, 1):
                            rc.spawn(loc, 1)
                return
            count += 1
            if first is None:
                first = rc.get_round()
            if count != rc.get_round() - first + 1:
                raise RuntimeError("module globals are shared between squads")
    """))
    replay = run_match(str(bot), str(bot), MatchConfig(seed=2, max_rounds=30))
    assert replay["stats"][0]["squads_spawned"] >= 3
    assert replay["stats"][0]["exceptions"] == 0 and replay["stats"][1]["exceptions"] == 0
