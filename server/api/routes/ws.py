import json
import jwt
import redis.asyncio as redis
from asyncio import Lock
from typing import Literal
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from server.config import settings


def judge(move1: str, move2: str) -> str:
    if move1 == move2:
        return "draw"
    if move1 == "rock" and move2 == "scissors":
        return "win"
    if move1 == "scissors" and move2 == "paper":
        return "win"
    if move1 == "paper" and move2 == "rock":
        return "win"
    return "lose"


class JoinedEventData(BaseModel):
    t: str
    room: str


class ResultEventData(BaseModel):
    your_move: str
    opp_move: str
    outcome: str


class MoveEventData(BaseModel):
    tg_id: int
    round_id: str
    move: str
    ts: int


class ReadyEventData(BaseModel):
    room: str


class BaseEventMessage(BaseModel):
    type: Literal["JOINED", "MOVE", "RESULT", "READY"]


class Joined(BaseEventMessage):
    data: JoinedEventData


class Move(BaseEventMessage):
    data: MoveEventData


class Player(BaseModel):
    tg_id: int
    move: str | None = None
    score: int = 0


class RoomState(BaseModel):
    room_name: str
    round_id: str
    moves: dict[int, str | None] = {}  # tg_id => move
    scores: dict[int, int] = {}  # tg_id => score


ws_router = APIRouter(tags=["websocket"])
redis_client = redis.Redis.from_url(settings.REDIS_URI, decode_responses=True)


class ConnectionManager:
    def __init__(self):
        """
        active_connections: room_name -> list of WebSocket connections
        room_players: room_name -> dict[tg_id -> WebSocket]
        room_state: room_name -> RoomState
        room_locks: room_name -> Lock
        """
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.room_players: dict[str, dict[int, WebSocket]] = {}
        self.room_state: dict[str, RoomState] = {}
        self.room_locks: dict[str, Lock] = {}

    async def connect(self, room_name: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(room_name, []).append(websocket)
        self.room_players.setdefault(room_name, {})
        self.room_state.setdefault(room_name, RoomState(
            room_name=room_name, round_id=room_name,
            moves={}, scores={}
        ))
        self.room_locks.setdefault(room_name, Lock())
    
    def register_player(self, room_name: str, tg_id: int, websocket: WebSocket):
        self.room_players.setdefault(room_name, {})[tg_id] = websocket
        state = self.room_state.setdefault(room_name, RoomState(room_name=room_name, round_id=room_name))
        state.moves.setdefault(tg_id, None)
        state.scores.setdefault(tg_id, 0)

    async def round_start(self, room_name: str):
        state = self.room_state[room_name]
        for k in list(state.moves.keys()):
            state.moves[k] = None
        await self.send_message_to_room(
            room_name=room_name,
            message={"type": "ROUND_START", "data": {"round_id": room_name}},
        )

    def room_is_ready(self, room_name: str) -> bool:
        connections = self.active_connections.get(room_name, [])
        return len(connections) >= 2

    async def send_message_to_room(self, *, room_name: str, message: dict):
        connections = self.active_connections.get(room_name, [])
        for ws in connections:
            await ws.send_json(message)

    async def send_personal_message(self, *, room_name: str, tg_id: int, message: dict):
        websocket = self.room_players.get(room_name, {}).get(tg_id)
        if websocket:
            await websocket.send_json(message)

    async def receive_move(self, *, room_name: str, tg_id: int, move: str):
        move = move.lower().strip()
        if move not in ["rock", "paper", "scissors"]:
            await self.send_personal_message(
                room_name=room_name,
                tg_id=tg_id,
                message={"type": "ERROR", "data": {"reason": "Invalid move."}},
            )
            return

        lock = self.room_locks[room_name]
        async with lock:
            state = self.room_state[room_name]
            state.moves[tg_id] = move

            if not self.room_is_ready(room_name):
                await self.send_personal_message(
                    room_name=room_name,
                    tg_id=tg_id,
                    message={"type": "WAIT_FOR_OPPONENT", "data": {"room_id": room_name}},
                )
                return
            
            moves_done = {pid: mv for pid, mv in state.moves.items() if mv is not None}

            if len(moves_done) < 2:
                await self.send_message_to_room(
                    room_name=room_name,
                    message={"type": "WAIT_FOR_OPPONENT", "data": {"room_id": room_name}},
                )
                return

            p1, p2 = list(moves_done.keys())
            m1, m2 = moves_done[p1], moves_done[p2]

            outcome_p1 = judge(m1, m2)
            outcome_p2 = "draw" if outcome_p1 == "draw" else ("lose" if outcome_p1 == "win" else "win")

            state.scores[p1] += 1 if outcome_p1 == "win" else 0
            state.scores[p2] += 1 if outcome_p2 == "win" else 0

            await self.send_personal_message(
                room_name=room_name,
                tg_id=p1,
                message={
                    "type": "RESULT",
                    "data": {"p1": p1, "p2": p2, "m1": m1, "m2": m2, "outcome_p1": outcome_p1, "outcome_p2": outcome_p2}
                }
            )
            await self.send_personal_message(
                room_name=room_name,
                tg_id=p2,
                message={
                    "type": "RESULT",
                    "data": {"p1": p1, "p2": p2, "m1": m1, "m2": m2, "outcome_p1": outcome_p1, "outcome_p2": outcome_p2}
                }
            )

            state.moves[p1] = None
            state.moves[p2] = None
        
        await self.round_start(room_name=room_name)

    async def disconnect(self, room_name: str, websocket: WebSocket):
        if room_name in self.active_connections:
            self.active_connections[room_name].remove(websocket)
            if not self.active_connections[room_name]:
                del self.active_connections[room_name]


manager = ConnectionManager()


@ws_router.websocket("/ws/rooms/{room_name}")
async def websocket_rps_endpoint(websocket: WebSocket, room_name: str):
    """WebSocket endpoint for Rock-Paper-Scissors game.

    Args:
        websocket (WebSocket): The WebSocket connection.
    """
    await manager.connect(room_name, websocket)

    try:
        while True:
            json_event_data = await websocket.receive_json()
            match json_event_data["type"]:
                case "JOINED":
                    room_join_event = Joined.model_validate(json_event_data)

                    try:
                        user_data = jwt.decode(room_join_event.data.t, settings.SECRET_KEY, algorithms=['HS256'])
                    except jwt.PyJWTError:
                        await websocket.send_json({"type": "AUTH_FAIL", "data": {"message": "Invalid token"}})
                        await websocket.close(code=4401)
                        return

                    tg_id = int(user_data.get('tg_id') or user_data.get('user_id'))
                    if not tg_id:
                        await websocket.send_json({"type": "AUTH_FAIL", "data": {"message": "Invalid token"}})
                        await websocket.close(code=4401)
                        return

                    manager.register_player(room_name, tg_id, websocket)
                    await websocket.send_json({"type": "AUTH_OK", "data": {"tg_id": tg_id}})

                    if manager.room_is_ready(room_name):
                        await manager.round_start(room_name=room_name)
                    else:
                        await websocket.send_json({"type": "WAIT_FOR_OPPONENT", "data": {"room_id": room_name}})

                case "MOVE":
                    player_move = Move.model_validate(json_event_data)
                    room_players = manager.room_players.get(room_name, {})

                    if player_move.data.tg_id not in room_players:
                        await websocket.send_json({"type": "AUTH_FAIL", "data": {"message": "Invalid player"}})
                        await websocket.close(code=4401)
                        return

                    if not manager.room_is_ready(room_name):
                        await websocket.send_json({"type": "WAIT_FOR_OPPONENT", "data": {"room_id": room_name}})
                        continue

                    await manager.receive_move(
                        room_name=room_name,
                        tg_id=player_move.data.tg_id,
                        move=player_move.data.move
                    )

                case "READY":
                    await manager.round_start(room_name=room_name)

    except WebSocketDisconnect:
        await manager.disconnect(room_name, websocket)
