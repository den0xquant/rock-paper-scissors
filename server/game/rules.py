from typing import Literal
from pydantic import BaseModel


class WsEvent(BaseModel):
    type: Literal["auth", "move", "ready"]


class AuthEvent(WsEvent):
    username: str
    user_id: str