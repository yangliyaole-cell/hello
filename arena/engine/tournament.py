"""Round-robin tournaments. Every match runs in a fresh process with a memory cap and
a wall-clock watchdog, so a bot that crashes the interpreter or hangs inside a long C
call loses that match instead of taking the tournament down."""

from __future__ import annotations

import itertools
import json
import math
import multiprocessing as mp
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .match import MatchConfig


@dataclass
class GameRecord:
    a: str
    b: str
    seed: int
    winner: int | None      # 0 = a, 1 = b, None = draw / no result
    reason: str
    rounds: int
    stats: list | None
    replay: str | None


def _child(conn, bot_a, bot_b, cfg_dict, replay_path, current, mem_mb):
    try:
        if mem_mb:
            try:
                import resource
                limit = mem_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ImportError, ValueError, OSError):
                pass
        from .match import MatchConfig as _Cfg, run_match

        def on_turn(team):
            current.value = team

        replay = run_match(bot_a, bot_b, _Cfg(**cfg_dict), on_turn=on_turn)
        if replay_path:
            Path(replay_path).parent.mkdir(parents=True, exist_ok=True)
            with open(replay_path, "w", encoding="utf-8") as f:
                json.dump(replay, f, separators=(",", ":"))
        conn.send({"ok": True, "result": replay["result"], "stats": replay["stats"]})
    except BaseException as e:  # noqa: BLE001
        conn.send({"ok": False, "error": f"{type(e).__name__}: {e}", "team": current.value})
    finally:
        conn.close()


def run_isolated(bot_a: str, bot_b: str, cfg: MatchConfig, replay_path: str | None = None,
                 timeout: float = 600, mem_mb: int = 2048) -> GameRecord:
    """Run one match in a subprocess. On a crash or hang, the team that was running loses."""
    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    current = ctx.Value("i", -1, lock=False)
    p = ctx.Process(target=_child, args=(send, str(bot_a), str(bot_b), asdict(cfg), replay_path, current, mem_mb),
                    daemon=True)
    p.start()
    send.close()
    msg = None
    try:
        if recv.poll(timeout):
            msg = recv.recv()
    except EOFError:
        msg = None
    if p.is_alive():
        p.kill()
    p.join(5)
    na, nb = Path(bot_a).name, Path(bot_b).name
    if msg and msg.get("ok"):
        r = msg["result"]
        return GameRecord(na, nb, cfg.seed, r["winner"], r["reason"], r["rounds"], msg["stats"], replay_path)
    team = msg.get("team", current.value) if msg else current.value
    why = msg["error"] if msg else (f"no result within {timeout}s" if p.exitcode is None or p.exitcode < 0 else f"process exited with code {p.exitcode}")
    if team in (0, 1):
        return GameRecord(na, nb, cfg.seed, 1 - team, f"match aborted while {'AB'[team]} was running ({why})", 0, None, None)
    return GameRecord(na, nb, cfg.seed, None, f"match aborted ({why})", 0, None, None)


def bradley_terry(names: list[str], games: list[tuple[int, int, float]], iters: int = 300) -> dict[str, float]:
    """Elo-scaled Bradley-Terry ratings. Draws count as half a win.
    One virtual draw per pair keeps undefeated players finite."""
    n = len(names)
    wins = [0.0] * n
    count = [[0.0] * n for _ in range(n)]
    for i, j, s in games:
        wins[i] += s
        wins[j] += 1 - s
        count[i][j] += 1
        count[j][i] += 1
    for i in range(n):
        for j in range(n):
            if i != j:
                count[i][j] += 1
        wins[i] += 0.5 * (n - 1)
    p = [1.0] * n
    for _ in range(iters):
        new = []
        for i in range(n):
            denom = sum(count[i][j] / (p[i] + p[j]) for j in range(n) if j != i)
            new.append(wins[i] / denom if denom else p[i])
        g = math.exp(sum(math.log(v) for v in new) / n)
        p = [v / g for v in new]
    return {names[i]: 1500 + 400 * math.log10(p[i]) for i in range(n)}


def run_tournament(bots: list[str], games_per_pair: int = 2, seed: int = 1, jobs: int = 2,
                   max_rounds: int | None = None, time_limit: float = 0.1, out_dir: str | None = None,
                   save_replays: bool = True, timeout: float = 600, progress=print) -> dict:
    names = [Path(b).name for b in bots]
    if len(set(names)) != len(names):
        raise ValueError("bot names (directory names) must be unique")
    out = Path(out_dir) if out_dir else None
    tasks = []
    for (i, a), (j, b) in itertools.combinations(enumerate(bots), 2):
        for g in range(games_per_pair):
            s = seed + g // 2
            ia, ib = (i, j) if g % 2 == 0 else (j, i)
            cfg = MatchConfig(seed=s, time_limit=time_limit)
            if max_rounds:
                cfg.max_rounds = max_rounds
            rp = str(out / "replays" / f"{names[ia]}-vs-{names[ib]}-s{s}.json") if out and save_replays else None
            tasks.append((ia, ib, bots[ia], bots[ib], cfg, rp))

    t0 = time.time()
    records: list[tuple[int, int, GameRecord]] = []

    def work(task):
        ia, ib, pa, pb, cfg, rp = task
        return ia, ib, run_isolated(pa, pb, cfg, rp, timeout=timeout)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for k, (ia, ib, rec) in enumerate(pool.map(work, tasks), 1):
            records.append((ia, ib, rec))
            w = "draw" if rec.winner is None else (rec.a if rec.winner == 0 else rec.b)
            if progress:
                progress(f"[{k}/{len(tasks)}] {rec.a} vs {rec.b} (seed {rec.seed}): {w} - {rec.reason}")

    table = {n: {"name": n, "games": 0, "wins": 0, "draws": 0, "losses": 0, "turns": 0, "instructions": 0,
                 "exceptions": 0, "budget_exceeded": 0, "timeouts": 0} for n in names}
    bt_games = []
    for ia, ib, rec in records:
        for idx, side in ((ia, 0), (ib, 1)):
            row = table[names[idx]]
            row["games"] += 1
            if rec.winner is None:
                row["draws"] += 1
            elif rec.winner == side:
                row["wins"] += 1
            else:
                row["losses"] += 1
            if rec.stats:
                st = rec.stats[side]
                for key in ("turns", "instructions", "exceptions", "budget_exceeded", "timeouts"):
                    row[key] += st[key]
        bt_games.append((ia, ib, 0.5 if rec.winner is None else 1.0 - rec.winner))
    ratings = bradley_terry(names, bt_games)
    standings = []
    for n in names:
        row = table[n]
        row["rating"] = round(ratings[n])
        row["win_rate"] = round((row["wins"] + 0.5 * row["draws"]) / row["games"], 3) if row["games"] else 0
        row["avg_instructions_per_turn"] = round(row["instructions"] / row["turns"]) if row["turns"] else 0
        standings.append(row)
    standings.sort(key=lambda r: (-r["rating"], r["name"]))
    summary = {
        "bots": names,
        "games_per_pair": games_per_pair,
        "seed": seed,
        "elapsed_seconds": round(time.time() - t0, 1),
        "standings": standings,
        "games": [asdict(rec) for _, _, rec in records],
    }
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (out / "summary.md").write_text(format_standings(summary), encoding="utf-8")
    return summary


def format_standings(summary: dict) -> str:
    lines = [
        "| # | bot | rating | W | D | L | win rate | avg instr/turn | exceptions | over budget | timeouts |",
        "|---|-----|-------:|--:|--:|--:|---------:|---------------:|-----------:|------------:|---------:|",
    ]
    for k, r in enumerate(summary["standings"], 1):
        lines.append(
            f"| {k} | {r['name']} | {r['rating']} | {r['wins']} | {r['draws']} | {r['losses']} | "
            f"{r['win_rate']:.0%} | {r['avg_instructions_per_turn']} | {r['exceptions']} | "
            f"{r['budget_exceeded']} | {r['timeouts']} |"
        )
    return "\n".join(lines) + "\n"
