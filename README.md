# Arena：AI 之间的编程对战平台

一个类 MIT Battlecode 的编程游戏平台，**只面向 AI**：让不同的大模型各自用 Python 写 bot，在同一张地图上对战，看谁更强。

- **固定 SDK**：bot 只能通过 `RobotController` 感知和行动，所有规则由引擎校验。
- **计算量上限**：像 Battlecode 的 bytecode limit 一样，每个机器人每回合有指令预算（单位 1 万条，HQ 2 万条），由引擎精确计数、强制执行，不靠模型自觉。
- **沙箱**：bot 不能读引擎状态、不能偷看对手、不能给共享类打补丁、机器人之间不能私下共享内存。
- **可复现**：同样的种子 + 同样的 bot = 完全相同的比赛。
- **可视化**：每场比赛生成回放 JSON 和一个单文件 HTML 查看器（播放、逐回合、点选机器人看血量 / 指令用量 / 调试信息、兵力曲线、问题列表）。
- **循环赛**：每场比赛独立子进程，交换先后手，输出胜负表和 Bradley–Terry 评分。

现在的游戏 **Ore Rush** 是占位规则（矿工采矿、士兵打仗、摧毁对方 HQ 获胜），具体竞赛形式之后再定。

## 快速开始

需要 Python 3.12+（用到了 `sys.monitoring`），没有第三方依赖（跑测试需要 `pytest`）。

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

`python -m arena run --help` 查看所有选项（地图尺寸、自定义地图、回合上限、CPU 时限、打印 bot 日志等）。

## 写一个 bot

```python
# bots/mybot/bot.py
from arena.sdk import *

def turn(rc):
    if rc.get_type() == RobotType.HQ:
        for d in Direction.all():
            if rc.can_build(RobotType.SOLDIER, d):
                rc.build(RobotType.SOLDIER, d)
                return
    ...
```

完整规则和 API 见 [docs/PLAYER_GUIDE.md](docs/PLAYER_GUIDE.md)（英文，直接作为给 AI 的说明书）。

## 让几个 AI 对战

1. 把 [docs/AI_PLAYER_PROMPT.md](docs/AI_PLAYER_PROMPT.md) 里的提示词发给每个模型，各自写 `bots/<模型名>/bot.py`。
2. 收集它们的 `bot.py`，用干净的引擎跑循环赛：

   ```bash
   python -m arena tournament bots/claude bots/gpt bots/gemini examplebot --games 6
   ```

3. 看 `results/latest/summary.md` 的排名，打开 `results/latest/replays/` 里的回放。

## 目录

```
arena/
  engine/      平台：沙箱 (sandbox.py)、指令计数 (meter.py)、对局 (match.py)、循环赛 (tournament.py)
  game/        游戏规则（占位）：地图、规则、RobotController、平衡参数 (constants.py)
  viewer/      单文件 HTML 回放查看器
  sdk.py       bot 唯一能 import 的本项目模块
bots/          基线 bot：examplebot、rusher、randombot
docs/          DESIGN.md（设计说明）、PLAYER_GUIDE.md（给 AI 的说明书）、AI_PLAYER_PROMPT.md
tests/         沙箱逃逸测试、对局 / 确定性 / 地图对称性测试
```

设计取舍、局限和待定事项见 [docs/DESIGN.md](docs/DESIGN.md)。
