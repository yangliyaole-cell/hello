"""The bot-facing SDK. In a bot: ``from arena.sdk import *``.

See docs/PLAYER_GUIDE.md for the full rules and API reference.
"""

from .game.controller import RobotController
from .game.types import (
    Direction,
    GameActionError,
    GameConstants,
    Location,
    OccupationInfo,
    SquadInfo,
    Team,
    TileInfo,
    TileKind,
    occupation_rounds,
)

__all__ = [
    "RobotController", "Direction", "Location", "Team", "TileKind", "TileInfo", "SquadInfo",
    "OccupationInfo", "GameActionError", "GameConstants", "occupation_rounds",
]
