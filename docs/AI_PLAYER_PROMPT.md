# 给 AI 参赛者的提示词模板

用法：
1. 给每个参赛模型一个干净的仓库副本，或者让它在独立目录里工作。
2. 把下面英文提示词里的 `<NAME>` 换成它的名字（比如 `claude`、`gpt`、`gemini`），`<LIMIT>` 换成统一的迭代上限。
3. 所有模型用同一段提示词、同样的上限，比赛才公平。

正式比赛只收集每个模型最新的 `bots/<NAME>/bot.py`，用干净的引擎跑：

```bash
python -m arena tournament bots/claude bots/gpt bots/gemini bots/examplebot --games 6
```

之后可以把比赛结果（只给它自己参与的对局的战报和数据，不给对手的代码和回放）反馈给它，让它继续改进。

---

```text
You are competing in Frontier, a turn-based territory game in which bots written by
different AI models play against each other. Your goal is to write the strongest bot you can.

1. Read docs/PLAYER_GUIDE.md completely. It contains every rule and the full API.
2. Write your bot in bots/<NAME>/bot.py (a single file defining turn(rc)).
3. Test and improve it with:
     python -m arena check bots/<NAME>
     python -m arena run bots/<NAME> bots/examplebot --seed <N>
     python -m arena run bots/examplebot bots/<NAME> --seed <N>
     python -m arena run bots/<NAME> bots/rusher --seed <N>
   Use several seeds and both sides. The match report lists exceptions with line
   numbers, budget overruns and timeouts.

Rules for this session:
- Only create or modify files under bots/<NAME>/. Official matches are run with an
  unmodified copy of the engine.
- Your bot must pass `python -m arena check` with no runtime problems.
- You have <LIMIT> to iterate.

When you are done, reply with a short description of your strategy and the final
contents of bots/<NAME>/bot.py.
```
