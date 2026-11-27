"""The bot-facing SDK. In a bot: ``from arena.sdk import *``.

See docs/PLAYER_GUIDE.md for the full rules and API reference.
"""

from .game.controller import RobotController
from .game.types import (
    Direction,
    GameActionError,
    GameConstants,
    Location,
    OreInfo,
    RobotInfo,
    RobotType,
    Team,
    Terrain,
)

__all__ = [
    "RobotController", "Direction", "Location", "Team", "RobotType", "Terrain",
    "RobotInfo", "OreInfo", "GameActionError", "GameConstants",
]
