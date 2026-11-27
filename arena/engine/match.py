"""Running one match between two bots and producing a replay."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass

from .. import __version__
from ..game import constants as C
from ..game.controller import new_controller, release, set_active
from ..game.maps import MapData, generate_map, load_map
from ..game.types import RobotType
from ..game.world import GameWorld, Robot
from .loader import BotProgram
from .meter import BUDGET, ERROR, METER, OK, TIMEOUT
from .sandbox import make_globals

MAX_ERRORS_PER_TEAM = 50
MAX_LOG_LINES = 5000
TEAM_NAMES = ("A", "B")


@dataclass
class MatchConfig:
    seed: int = 1
    max_rounds: int = C.MAX_ROUNDS
    map_file: str | None = None
    map_width: int | None = None
    map_height: int | None = None
    time_limit: float = 0.1     # CPU seconds per robot turn (backstop for C-level work)
    record: bool = True         # store per-round snapshots in the replay


class _RobotRunner:
    __slots__ = ("program", "robot", "rc", "globals", "turn_fn", "broken", "module_name", "rng_seed", "log_chars")

    def __init__(self, program: BotProgram, robot: Robot, world: GameWorld, seed: int):
        self.program = program
        self.robot = robot
        self.rc = new_controller(world, robot)
        self.globals = None
        self.turn_fn = None
        self.broken = False
        self.module_name = f"bot_{TEAM_NAMES[robot.team]}_{robot.id}"
        # string seeds hash with sha512 -> independent of PYTHONHASHSEED
        self.rng_seed = f"{seed}:{robot.team}:{robot.spawn_index}"
        self.log_chars = 0


def _format_exception(exc: BaseException, filename: str) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == filename]
    lines = [f"{type(exc).__name__}: {exc}"]
    for f in frames[-6:]:
        lines.append(f"  line {f.lineno}, in {f.name}: {(f.line or '').strip()}")
    return "\n".join(lines)


class Match:
    def __init__(self, bot_a: BotProgram, bot_b: BotProgram, config: MatchConfig | None = None, on_turn=None):
        self.programs = (bot_a, bot_b)
        self.cfg = config or MatchConfig()
        self.on_turn = on_turn
        self.errors: list = []
        self._error_counts = [0, 0]
        self.logs: list = []
        self.world: GameWorld | None = None

    # ------------------------------------------------------------------ helpers

    def _map(self) -> MapData:
        if self.cfg.map_file:
            return load_map(self.cfg.map_file)
        return generate_map(self.cfg.seed, self.cfg.map_width, self.cfg.map_height)

    def _record_error(self, runner: _RobotRunner, kind: str, message: str) -> None:
        t = runner.robot.team
        if self._error_counts[t] >= MAX_ERRORS_PER_TEAM:
            return
        self._error_counts[t] += 1
        self.errors.append({
            "round": self.world.round, "team": t, "robot": runner.robot.id,
            "type": runner.robot.type.name, "kind": kind, "message": message,
        })

    def _sink_for(self, runner: _RobotRunner):
        def sink(text: str) -> None:
            room = C.LOG_MAX_CHARS_PER_TURN - runner.log_chars
            if room <= 0 or len(self.logs) >= MAX_LOG_LINES:
                return
            text = text[:room]
            runner.log_chars += len(text)
            self.logs.append([self.world.round, runner.robot.team, runner.robot.id, text.rstrip("\n")])
        return sink

    def _handle_status(self, runner: _RobotRunner, status: str, exc, phase: str) -> None:
        robot = runner.robot
        st = self.world.stats[robot.team]
        if status == OK:
            return
        if status == ERROR:
            st["exceptions"] += 1
            self.world.events.append(["err", robot.id])
            self._record_error(runner, f"{phase}-exception", _format_exception(exc, runner.program.filename))
        elif status == BUDGET:
            st["budget_exceeded"] += 1
            self.world.events.append(["over", robot.id])
            if st["budget_exceeded"] <= 5:
                limit = C.INIT_BUDGET if phase == "init" else robot.type.budget
                self._record_error(runner, f"{phase}-budget", f"instruction budget of {limit} exceeded; turn ended early")
        elif status == TIMEOUT:
            st["timeouts"] += 1
            self.world.events.append(["tle", robot.id])
            if st["timeouts"] <= 5:
                self._record_error(runner, f"{phase}-timeout", f"CPU time limit of {self.cfg.time_limit}s per turn exceeded")

    def _turn(self, runner: _RobotRunner) -> None:
        robot = runner.robot
        world = self.world
        robot.indicator = robot.dots = robot.lines = None
        runner.log_chars = 0
        used_total = 0
        set_active(runner.rc, True)
        try:
            if runner.globals is None:
                runner.globals = make_globals(runner.module_name, runner.rng_seed, self._sink_for(runner), C.API_COST["log"])
                status, used, exc = METER.run(exec, (runner.program.code, runner.globals), C.INIT_BUDGET, self.cfg.time_limit * 5)
                used_total += used
                self._handle_status(runner, status, exc, "init")
                fn = runner.globals.get("turn")
                if status != OK or not callable(fn):
                    runner.broken = True
                    if status == OK:
                        self._record_error(runner, "init-exception", "module does not define a callable turn(rc)")
                else:
                    runner.turn_fn = fn
            if runner.turn_fn is not None:
                status, used, exc = METER.run(runner.turn_fn, (runner.rc,), robot.type.budget, self.cfg.time_limit)
                used_total += used
                self._handle_status(runner, status, exc, "turn")
        finally:
            set_active(runner.rc, False)
        robot.used = used_total
        st = world.stats[robot.team]
        st["turns"] += 1
        st["instructions"] += used_total

    # ------------------------------------------------------------------ main loop

    def run(self) -> dict:
        t0 = time.time()
        a, b = self.programs
        load_errors = [p.errors for p in (a, b)]
        mapdata = self._map()
        self.world = world = GameWorld(mapdata, self.cfg.seed, self.cfg.max_rounds)
        rounds: list = []
        initial = world.snapshot()

        if not a.ok or not b.ok:
            if a.ok:
                world.winner, world.reason = 0, "opponent's bot failed to load"
            elif b.ok:
                world.winner, world.reason = 1, "opponent's bot failed to load"
            else:
                world.reason = "both bots failed to load"
            return self._replay(initial, rounds, load_errors, time.time() - t0, draw=not a.ok and not b.ok)

        runners: dict[int, _RobotRunner] = {}
        while not world.over:
            world.begin_round()
            for robot in world.turn_order():
                if not robot.alive:
                    continue
                runner = runners.get(robot.id)
                if runner is None:
                    runner = runners[robot.id] = _RobotRunner(self.programs[robot.team], robot, world, self.cfg.seed)
                if self.on_turn is not None:
                    self.on_turn(robot.team)
                self._turn(runner)
                if world.over:
                    break
            world.end_round()
            for rid in [rid for rid, rn in runners.items() if not rn.robot.alive]:
                release(runners.pop(rid).rc)
            if self.cfg.record:
                rounds.append(world.snapshot())
        for rn in runners.values():
            release(rn.rc)
        return self._replay(initial, rounds, load_errors, time.time() - t0)

    def _replay(self, initial, rounds, load_errors, elapsed, draw=False) -> dict:
        w = self.world
        names = [p.name for p in self.programs]
        winner = None if draw else w.winner
        stats = w.stats
        for st in stats:
            st["avg_instructions_per_turn"] = round(st["instructions"] / st["turns"]) if st["turns"] else 0
        return {
            "format": "arena-replay",
            "version": 1,
            "engine": __version__,
            "seed": self.cfg.seed,
            "teams": [{"name": names[0], "side": "A"}, {"name": names[1], "side": "B"}],
            "map": w.map.to_json(),
            "types": [
                {"name": t.name, "letter": t.name[0], "cost": t.cost, "health": t.health,
                 "vision_radius_sq": t.vision_radius_sq, "action_radius_sq": t.action_radius_sq,
                 "budget": t.budget}
                for t in RobotType.all()
            ],
            "initial": initial,
            "rounds": rounds,
            "result": {
                "winner": winner,
                "winner_name": None if winner is None else names[winner],
                "reason": w.reason,
                "rounds": w.round,
            },
            "stats": stats,
            "load_errors": load_errors,
            "errors": self.errors,
            "logs": self.logs,
            "elapsed_seconds": round(elapsed, 2),
        }


def run_match(bot_a, bot_b, config: MatchConfig | None = None, on_turn=None) -> dict:
    """Run a match. ``bot_a``/``bot_b`` are paths or BotProgram objects. Returns the replay dict."""
    pa = bot_a if isinstance(bot_a, BotProgram) else BotProgram.from_path(bot_a)
    pb = bot_b if isinstance(bot_b, BotProgram) else BotProgram.from_path(bot_b)
    return Match(pa, pb, config, on_turn).run()
