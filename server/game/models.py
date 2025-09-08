from enum import Enum
from dataclasses import dataclass, field

from server.game.states import PlayerState


class Move(Enum):
    ROCK = "rock"
    PAPER = "paper"
    SCISSORS = "scissors"


class Outcome(Enum):
    WIN = "win"
    LOSE = "lose"
    DRAW = "draw"


@dataclass
class PlayerCtx:
    pid: str
    state: PlayerState = PlayerState.DISCONNECTED
    last_move: Move | None = None
    score: int = 0


@dataclass
class RoomCtx:
    name: str
    best_of: int = 5
    round_id: int = 0
    players: dict[str, PlayerCtx] = field(default_factory=dict)
    last_result: str | None = None
