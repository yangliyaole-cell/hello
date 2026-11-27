# 给 AI 参赛者的提示词模板

用法：给每个参赛模型一个干净的仓库副本（或者让它在独立目录里工作），把下面这段英文提示词里的
`<NAME>` 换成它的名字（比如 `claude`、`gpt`、`gemini`），然后让它开始工作。所有模型用同一段提示词、
同样的迭代上限，比赛才公平。

正式比赛时只收集每个模型的 `bots/<NAME>/bot.py`，用干净的引擎跑：

```bash
python -m arena tournament bots/claude bots/gpt bots/gemini bots/examplebot --games 6
```

---

```text
You are competing in Ore Rush, a Battlecode-style programming game in which bots written
by different AI models fight each other. Your goal is to write the strongest bot you can.

1. Read docs/PLAYER_GUIDE.md completely. It contains every rule and the full API.
2. Write your bot in bots/<NAME>/bot.py (a single file defining turn(rc)).
3. Test and improve it with:
     python -m arena check bots/<NAME>
     python -m arena run bots/<NAME> bots/examplebot --seed <N>
     python -m arena run bots/examplebot bots/<NAME> --seed <N>
     python -m arena run bots/<NAME> bots/rusher --seed <N>
   Use several seeds and both sides. Read the match reports: they list exceptions with
   line numbers, budget overruns and timeouts.

Rules for this session:
- Only create or modify files under bots/<NAME>/. Do not change the engine (arena/),
  other bots, tests or docs. Official matches are run with an unmodified engine.
- Your bot must pass `python -m arena check` with no runtime problems.
- Respect the spirit of the game: no attempts to escape the sandbox or the compute
  budget. They are blocked and logged, and count against you.
- You have <LIMIT> (for example: 2 hours / 30 test runs) to iterate.

When you are done, reply with a short description of your strategy and the final
contents of bots/<NAME>/bot.py.
```
