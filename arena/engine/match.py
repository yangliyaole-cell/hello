"""Running one match between two bots and producing a replay."""

from __future__ import annotations

import gc
import time
import traceback
from dataclasses import dataclass

from .. import __version__
from ..game import constants as C
from ..game.controller import new_controller, release, set_active
from ..game.maps import MapData, generate_map, load_map
from ..game.world import Agent, GameWorld
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
    time_limit: float = 0.2     # CPU seconds per agent turn (backstop for C-level work)
    record: bool = True         # store per-turn frames in the replay


class _Runner:
    """One program instance: a squad's or HQ's private copy of the team's bot module."""

    __slots__ = ("program", "agent", "rc", "globals", "turn_fn", "module_name", "rng_seed", "log_chars")

    def __init__(self, program: BotProgram, agent: Agent, world: GameWorld, seed: int):
        self.program = program
        self.agent = agent
        self.rc = new_controller(world, agent)
        self.globals = None
        self.turn_fn = None
        self.module_name = f"bot_{TEAM_NAMES[agent.team]}_{agent.id}"
        # string seeds hash with sha512 -> independent of PYTHONHASHSEED
        self.rng_seed = f"{seed}:{agent.team}:{agent.spawn_index}"
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

    def _map(self) -> MapData:
        if self.cfg.map_file:
            return load_map(self.cfg.map_file)
        return generate_map(self.cfg.seed, self.cfg.map_width, self.cfg.map_height)

    def _record_error(self, runner: _Runner, kind: str, message: str) -> None:
        t = runner.agent.team
        if self._error_counts[t] >= MAX_ERRORS_PER_TEAM:
            return
        self._error_counts[t] += 1
        self.errors.append({
            "round": self.world.round, "turn": self.world.turn, "team": t, "agent": runner.agent.id,
            "type": "HQ" if runner.agent.is_hq else "SQUAD", "kind": kind, "message": message,
        })

    def _sink_for(self, runner: _Runner):
        def sink(text: str) -> None:
            room = C.LOG_MAX_CHARS_PER_TURN - runner.log_chars
            if room <= 0 or len(self.logs) >= MAX_LOG_LINES:
                return
            text = text[:room]
            runner.log_chars += len(text)
            self.logs.append([self.world.turn, runner.agent.team, runner.agent.id, text.rstrip("\n")])
        return sink

    def _handle_status(self, runner: _Runner, status: str, exc, phase: str) -> None:
        agent = runner.agent
        st = self.world.stats[agent.team]
        if status == OK:
            return
        if status == ERROR:
            st["exceptions"] += 1
            self.world.note(agent, "err")
            self._record_error(runner, f"{phase}-exception", _format_exception(exc, runner.program.filename))
        elif status == BUDGET:
            st["budget_exceeded"] += 1
            self.world.note(agent, "over")
            if st["budget_exceeded"] <= 5:
                limit = C.INIT_BUDGET if phase == "init" else (C.HQ_BUDGET if agent.is_hq else C.SQUAD_BUDGET)
                self._record_error(runner, f"{phase}-budget", f"instruction budget of {limit} exceeded; turn ended early")
        elif status == TIMEOUT:
            st["timeouts"] += 1
            self.world.note(agent, "tle")
            if st["timeouts"] <= 5:
                self._record_error(runner, f"{phase}-timeout", f"CPU time limit of {self.cfg.time_limit}s per turn exceeded")

    def _turn(self, runner: _Runner) -> None:
        agent = runner.agent
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
                if status == OK and callable(fn):
                    runner.turn_fn = fn
                elif status == OK:
                    self._record_error(runner, "init-exception", "module does not define a callable turn(rc)")
            if runner.turn_fn is not None and agent.alive:
                budget = C.HQ_BUDGET if agent.is_hq else C.SQUAD_BUDGET
                status, used, exc = METER.run(runner.turn_fn, (runner.rc,), budget, self.cfg.time_limit)
                used_total += used
                self._handle_status(runner, status, exc, "turn")
        finally:
            set_active(runner.rc, False)
        agent.used = used_total
        st = self.world.stats[agent.team]
        st["turns"] += 1
        st["instructions"] += used_total

    def run(self) -> dict:
        t0 = time.time()
        a, b = self.programs
        load_errors = [p.errors for p in (a, b)]
        self.world = world = GameWorld(self._map(), self.cfg.seed, self.cfg.max_rounds)
        initial = world.initial_frame()
        frames: list = []

        if not a.ok or not b.ok:
            if a.ok:
                world.winner, world.reason = 0, "opponent's bot failed to load"
            elif b.ok:
                world.winner, world.reason = 1, "opponent's bot failed to load"
            else:
                world.reason = "both bots failed to load"
            return self._replay(initial, frames, load_errors, time.time() - t0, draw=not a.ok and not b.ok)

        runners: dict[int, _Runner] = {}
        # Garbage collection pauses would be billed to whichever bot happens to be running,
        # so collect between turns instead, outside the timed sections.
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            self._loop(world, runners, frames)
        finally:
            if gc_was_enabled:
                gc.enable()
        return self._replay(initial, frames, load_errors, time.time() - t0)

    def _loop(self, world: GameWorld, runners: dict, frames: list) -> None:
        while not world.over:
            team = world.begin_turn()
            for agent in world.turn_agents():
                if not agent.alive:
                    continue  # merged into another squad earlier this turn
                runner = runners.get(agent.id)
                if runner is None:
                    runner = runners[agent.id] = _Runner(self.programs[team], agent, world, self.cfg.seed)
                if self.on_turn is not None:
                    self.on_turn(team)
                world.before_agent_turn(agent)
                self._turn(runner)
                world.after_agent_turn(agent)
            world.end_turn()
            for aid in world.gone:
                rn = runners.pop(aid, None)
                if rn is not None:
                    release(rn.rc)
            if self.cfg.record:
                frames.append(world.frame())
            if world.turn % 20 == 0:
                gc.collect(1 if world.turn % 400 else 2)
        for rn in runners.values():
            release(rn.rc)

    def _replay(self, initial, frames, load_errors, elapsed, draw=False) -> dict:
        w = self.world
        names = [p.name for p in self.programs]
        winner = None if draw else w.winner
        stats = w.stats
        for t, st in enumerate(stats):
            st["avg_instructions_per_turn"] = round(st["instructions"] / st["turns"]) if st["turns"] else 0
            st["final_territory"] = w.tiles[t]
            st["final_gold"] = w.gold[t]
            st["final_soldiers"] = w.soldiers[t] + w.hq[t].n
        return {
            "format": "arena-replay",
            "version": 2,
            "game": "frontier",
            "engine": __version__,
            "seed": self.cfg.seed,
            "teams": [{"name": names[0], "side": "A"}, {"name": names[1], "side": "B"}],
            "map": w.map.to_json(),
            "rules": {
                "max_rounds": w.max_rounds, "hq_garrison": C.HQ_GARRISON, "squad_budget": C.SQUAD_BUDGET,
                "hq_budget": C.HQ_BUDGET, "vision_radius": C.VISION_RADIUS,
            },
            "initial": initial,
            "frames": frames,
            "result": {
                "winner": winner,
                "winner_name": None if winner is None else names[winner],
                "reason": w.reason,
                "rounds": w.round,
                "turns": w.turn,
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
