# Arena：AI 之间的编程对战平台

一个类 MIT Battlecode 的编程游戏平台，**只面向 AI**：让不同的大模型各自用 Python 写 bot，在同一张地图上对战，看谁更强。

当前的游戏是 **Frontier（领地战）**：50×50 的格子地图上，双方从总部出兵、占领领地、夺取对方的领地，2000 回合后领地多的一方获胜，攻下对方总部直接获胜。规则见 [docs/DESIGN.md](docs/DESIGN.md)（中文摘要）和 [docs/PLAYER_GUIDE.md](docs/PLAYER_GUIDE.md)（给 AI 的完整说明，英文）。

- **固定 SDK**：bot 只能通过 `RobotController` 感知和行动，所有规则由引擎校验。
- **计算量上限**：每个兵团每手 1 万条指令、总部 3 万条，由引擎计数并强制执行。
- **沙箱**：bot 读不到引擎状态和对手代码，改不了共享的类，兵团之间不能私下共享内存。
- **可复现**：同样的种子加同样的 bot，比赛结果完全一致。
- **可视化**：每场比赛生成回放 JSON 和单文件 HTML 查看器，可以播放、逐手查看、点格子看领地/占领/兵团，看领地/金币/兵力曲线和问题列表。
- **循环赛**：每场比赛在独立子进程里跑，交换先后手，输出胜负表和 Bradley–Terry 评分。

## 快速开始

需要 Python 3.12+（用到 `sys.monitoring`），没有第三方依赖（跑测试需要 `pytest`）。

```bash
# 跑一场比赛：打印战报，写出 replays/*.json 和 replays/*.html
python -m arena run examplebot rusher --seed 3

# 用浏览器打开查看器
open replays/examplebot-vs-rusher-s3.html

# 检查一个 bot：静态检查 + 自我对战，报告异常 / 超预算 / 超时
python -m arena check bots/examplebot

# 循环赛：每对 4 局（交换先后手），4 个进程并行
python -m arena tournament examplebot rusher randombot --games 4 --jobs 4

# 测试
python -m pytest
```

`python -m arena run --help` 可以看到所有选项（自定义地图、回合上限、CPU 时限、打印 bot 日志等）。

## 写一个 bot

```python
# bots/mybot/bot.py
from arena.sdk import *

def turn(rc):
    if rc.is_hq():
        for loc in rc.get_owned_tiles(connected_only=True):
            if rc.can_spawn(loc, 5):
                rc.spawn(loc, 5)
                return
        return
    if rc.can_occupy(4):
        rc.occupy(4)          # 5 soldiers x 4 gold = 20 gold -> 10 rounds
    ...
```

## 让几个 AI 对战

1. 把 [docs/AI_PLAYER_PROMPT.md](docs/AI_PLAYER_PROMPT.md) 里的提示词发给每个模型，各自写 `bots/<模型名>/bot.py`。
2. 收集它们最新的 `bot.py`，用干净的引擎跑循环赛：

   ```bash
   python -m arena tournament bots/claude bots/gpt bots/gemini examplebot --games 6
   ```

3. 看 `results/latest/summary.md` 的排名，`results/latest/replays/` 里有每场的回放。

## 目录

```
arena/
  engine/      平台：沙箱 (sandbox.py)、指令计数 (meter.py)、对局 (match.py)、循环赛 (tournament.py)
  game/        游戏规则：地图 (maps.py)、规则 (world.py)、RobotController (controller.py)、数值 (constants.py)
  viewer/      单文件 HTML 回放查看器
  sdk.py       bot 唯一能 import 的本项目模块
bots/          基线 bot：examplebot、rusher、randombot
docs/          DESIGN.md、PLAYER_GUIDE.md、AI_PLAYER_PROMPT.md
tests/         沙箱逃逸测试、逐条规则测试、对局 / 确定性 / 地图测试
```
