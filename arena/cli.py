"""Command line interface: python -m arena {run,tournament,check,view}."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _ensure_hash_seed() -> None:
    """Fix str hashing so set iteration order (and so matches) are reproducible."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, "-m", "arena", *sys.argv[1:]])


def _resolve_bot(spec: str) -> str:
    p = Path(spec)
    if p.exists():
        return str(p)
    alt = Path("bots") / spec
    if alt.exists():
        return str(alt)
    raise SystemExit(f"bot not found: {spec} (tried {p} and {alt})")


def _size(s: str):
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 32x28")


def format_summary(replay: dict) -> str:
    teams = [t["name"] for t in replay["teams"]]
    res = replay["result"]
    m = replay["map"]
    out = [
        f"{teams[0]} (A) vs {teams[1]} (B) | seed {replay['seed']} | map {m['width']}x{m['height']} {m['symmetry']}",
    ]
    if res["winner"] is None:
        out.append(f"Result: no winner - {res['reason']}")
    else:
        out.append(f"Winner: {res['winner_name']} ({'AB'[res['winner']]}) - {res['reason']} [{res['rounds']} rounds]")
    for i, errs in enumerate(replay.get("load_errors", [])):
        for e in errs:
            out.append(f"  load error {teams[i]}: {e}")
    cols = [
        ("territory", lambda s: s["final_territory"]), ("gold", lambda s: s["final_gold"]),
        ("soldiers", lambda s: s["final_soldiers"]), ("spawned", lambda s: s["soldiers_spawned"]),
        ("claimed", lambda s: s["tiles_claimed"] + s["tiles_repaired"]), ("razed", lambda s: s["tiles_razed"]),
        ("killed", lambda s: s["enemy_soldiers_killed"]), ("instr/turn", lambda s: s["avg_instructions_per_turn"]),
        ("over-budget", lambda s: s["budget_exceeded"]), ("exceptions", lambda s: s["exceptions"]),
        ("timeouts", lambda s: s["timeouts"]),
    ]
    out.append(f"  {'bot':<16}" + "".join(f"{name:>12}" for name, _ in cols))
    for i, st in enumerate(replay["stats"]):
        out.append(f"  {teams[i]:<16}" + "".join(f"{fn(st):>12}" for _, fn in cols))
    errs = replay.get("errors", [])
    if errs:
        out.append(f"First {min(len(errs), 5)} of {len(errs)} recorded problems:")
        for e in errs[:5]:
            msg = e["message"].replace("\n", "\n      ")
            out.append(f"  - round {e['round']} {teams[e['team']]} {e['type']} #{e['agent']} [{e['kind']}]\n      {msg}")
    return "\n".join(out)


def cmd_run(args) -> int:
    from .engine.match import MatchConfig, run_match
    from .viewer import write_html

    a, b = _resolve_bot(args.bot_a), _resolve_bot(args.bot_b)
    cfg = MatchConfig(seed=args.seed, time_limit=args.time_limit, map_file=args.map)
    if args.rounds:
        cfg.max_rounds = args.rounds
    if args.size:
        cfg.map_width, cfg.map_height = args.size
    replay = run_match(a, b, cfg)
    print(format_summary(replay))
    out = Path(args.out or f"replays/{Path(a).name}-vs-{Path(b).name}-s{args.seed}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(replay, separators=(",", ":")), encoding="utf-8")
    print(f"Replay: {out}")
    if not args.no_html:
        html = out.with_suffix(".html")
        write_html(replay, html)
        print(f"Viewer: {html}  (open it in a browser)")
    if args.logs:
        for rnd, team, rid, text in replay["logs"]:
            print(f"[r{rnd} {'AB'[team]} #{rid}] {text}")
    return 0


def cmd_check(args) -> int:
    """Validate a bot and run it against itself for a few rounds."""
    from .engine.loader import BotProgram
    from .engine.match import MatchConfig, run_match

    path = _resolve_bot(args.bot)
    prog = BotProgram.from_path(path)
    if not prog.ok:
        print(f"FAIL: {prog.name} does not load:")
        for e in prog.errors:
            print(f"  {e}")
        return 1
    print(f"OK: {prog.name} passes static checks. Running a {args.rounds}-round mirror match...")
    replay = run_match(prog, BotProgram.from_path(path), MatchConfig(seed=args.seed, max_rounds=args.rounds))
    print(format_summary(replay))
    problems = sum(st["exceptions"] + st["budget_exceeded"] + st["timeouts"] for st in replay["stats"])
    print("PASS: no runtime problems." if problems == 0 else f"WARN: {problems} runtime problems (see above).")
    return 0 if problems == 0 else 2


def cmd_tournament(args) -> int:
    from .engine.tournament import format_standings, run_tournament

    bots = [_resolve_bot(b) for b in args.bots]
    if len(bots) < 2:
        raise SystemExit("need at least two bots")
    out = args.out or "results/latest"
    summary = run_tournament(
        bots, games_per_pair=args.games, seed=args.seed, jobs=args.jobs, max_rounds=args.rounds,
        time_limit=args.time_limit, out_dir=out, save_replays=not args.no_replays, timeout=args.timeout,
    )
    print()
    print(format_standings(summary))
    print(f"Results written to {out}/ (summary.md, summary.json{', replays/' if not args.no_replays else ''})")
    return 0


def cmd_view(args) -> int:
    from .viewer import write_html

    src = Path(args.replay)
    replay = json.loads(src.read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else src.with_suffix(".html")
    write_html(replay, out)
    print(f"Viewer: {out}")
    return 0


def main(argv=None) -> int:
    if argv is None:
        _ensure_hash_seed()
    p = argparse.ArgumentParser(prog="python -m arena", description="Battlecode-style arena for AI-written Python bots")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one match")
    r.add_argument("bot_a")
    r.add_argument("bot_b")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--rounds", type=int, help="round limit (default from game constants)")
    r.add_argument("--map", help="JSON map file instead of a generated map")
    r.add_argument("--size", type=_size, help="generated map size, e.g. 32x28")
    r.add_argument("--time-limit", type=float, default=0.2, help="CPU seconds per agent turn (default 0.2)")
    r.add_argument("--out", help="replay path (default replays/A-vs-B-sSEED.json)")
    r.add_argument("--no-html", action="store_true", help="don't write the HTML viewer next to the replay")
    r.add_argument("--logs", action="store_true", help="print the bots' print() output")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check", help="validate a bot and smoke-test it")
    c.add_argument("bot")
    c.add_argument("--rounds", type=int, default=300)
    c.add_argument("--seed", type=int, default=1)
    c.set_defaults(fn=cmd_check)

    t = sub.add_parser("tournament", help="round-robin between bots")
    t.add_argument("bots", nargs="+")
    t.add_argument("--games", type=int, default=2, help="games per pair (sides alternate; default 2)")
    t.add_argument("--seed", type=int, default=1)
    t.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    t.add_argument("--rounds", type=int)
    t.add_argument("--time-limit", type=float, default=0.2)
    t.add_argument("--timeout", type=float, default=600, help="wall-clock seconds per match before it is aborted")
    t.add_argument("--out", help="output directory (default results/latest)")
    t.add_argument("--no-replays", action="store_true")
    t.set_defaults(fn=cmd_tournament)

    v = sub.add_parser("view", help="build a standalone HTML viewer for a replay")
    v.add_argument("replay")
    v.add_argument("-o", "--out")
    v.set_defaults(fn=cmd_view)

    args = p.parse_args(argv)
    return args.fn(args)
