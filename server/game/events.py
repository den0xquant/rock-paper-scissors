from enum import Enum, auto


class ClientEvent(int, Enum):
    START = 1
    MOVE = 2
    READY = 3


class ServerEvent(int, Enum):
    ERROR = auto()
    ACK = auto()
    WAITING_OPP = auto()
    WAITING_MOVE = auto()
    READY = auto()
    RESULT = auto()
    MATCH_OVER = auto()
