from enum import Enum, auto


class RoomState(Enum):
    EMPTY = auto()
    ONE_WAITING = auto()
    ROUND_AWAIT_MOVES = auto()
    ROUND_RESULT = auto()
    MATCH_OVER = auto()


class PlayerState(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    READY = auto()
    MOVED = auto()
    BETWEEN_ROUNDS = auto()
