import jwt
import redis.asyncio as redis
import uuid
from asyncio import Lock
from typing import Literal
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

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
    moves: dict[int, str | None] = Field(default_factory=dict)   # tg_id -> move
    scores: dict[int, int] = Field(default_factory=dict)         # tg_id => score


ws_router = APIRouter(tags=["websocket"])
redis_client = redis.Redis.from_url(settings.REDIS_URI, decode_responses=True)


class ConnectionManager:
    def __init__(self):
        """
        active_connections: room_name -> list[WebSocket]
        room_players: room_name -> dict[tg_id -> WebSocket]
        room_state: room_name -> RoomState
        room_locks: room_name -> Lock

        lobby_waiting: очередь (tg_id, WebSocket) ожидающих
        lobby_ws2id: WebSocket -> tg_id
        lobby_id2ws: tg_id -> WebSocket
        lobby_lock: Lock
        """
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.room_players: dict[str, dict[int, WebSocket]] = {}
        self.room_state: dict[str, RoomState] = {}
        self.room_locks: dict[str, Lock] = {}
        self.room_name: str = self._new_room_name()

    # -------------------- ROOM --------------------

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(self.room_name, []).append(websocket)
        self.room_players.setdefault(self.room_name, {})
        self.room_state.setdefault(
            self.room_name,
            RoomState(room_name=self.room_name, round_id=self._new_round_id(), moves={}, scores={})
        )
        self.room_locks.setdefault(self.room_name, Lock())

    def register_player(self, tg_id: int, websocket: WebSocket):
        if not tg_id:
            raise ValueError("Invalid token")

        players = self.room_players.setdefault(self.room_name, {})
        players[tg_id] = websocket

        state = self.room_state.setdefault(
            self.room_name,
            RoomState(room_name=self.room_name, round_id=self._new_round_id())
        )
        state.moves.setdefault(tg_id, None)
        state.scores.setdefault(tg_id, 0)
        print(f"[join] room={self.room_name} players={list(players.keys())}")

    async def round_start(self):
        async with self.room_locks[self.room_name]:
            state = self.room_state[self.room_name]
            players = list(self.room_players.get(self.room_name, {}).keys())

            # завершён матч? сообщаем и ресетим
            if any(sc >= 3 for sc in state.scores.values()):
                await self.send_message_to_room(
                    room_name=self.room_name,
                    message={"type": "MATCH_OVER", "data": {"scores": state.scores, "round_id": state.round_id}}
                )
                state.scores = {pid: 0 for pid in players}

            # старт нового раунда
            state.round_id = self._new_round_id()
            state.moves = {pid: None for pid in players}

            if self.room_is_ready():
                await self.send_message_to_room(
                    room_name=self.room_name,
                    message={
                        "type": "ROUND_START",
                        "data": {
                            "scores": state.scores,
                            "round_id": state.round_id,
                            "timeout_sec": 10
                        }
                    },
                )

    def room_is_ready(self) -> bool:
        players = self.room_players.get(self.room_name, {})
        return len(players) >= 2

    async def send_message_to_room(self, *, room_name: str, message: dict):
        connections = self.active_connections.get(room_name, [])
        for ws in list(connections):
            try:
                await ws.send_json(message)
            except Exception:
                # best-effort cleanup
                try:
                    connections.remove(ws)
                except ValueError:
                    pass

    async def send_personal_message(self, *, tg_id: int, message: dict):
        websocket = self.room_players.get(self.room_name, {}).get(tg_id)
        if websocket:
            await websocket.send_json(message)

    async def receive_move(self, *, tg_id: int, move: str):
        move = move.lower().strip()
        if move not in {"rock", "paper", "scissors"}:
            await self.send_personal_message(
                tg_id=tg_id,
                message={"type": "ERROR", "data": {"reason": "Invalid move."}},
            )
            return

        lock = self.room_locks[self.room_name]
        async with lock:
            state = self.room_state[self.room_name]
            players = self.room_players.get(self.room_name, {})  # {tg_id: ws}

            if not self.room_is_ready():
                await self.send_personal_message(
                    tg_id=tg_id,
                    message={"type": "WAIT_FOR_OPPONENT", "data": {"room_id": self.room_name}},
                )
                return

            # записали ход
            state.moves[tg_id] = move

            # уведомим второго, что оппонент готов (если у него ещё нет хода)
            other_ids = [pid for pid in players.keys() if pid != tg_id]
            other_id = other_ids[0] if other_ids else None
            if other_id is not None and not state.moves.get(other_id):
                await self.send_personal_message(
                    tg_id=other_id,
                    message={"type": "OPP_READY", "data": {"room_id": self.room_name}},
                )

            # оба сходили?
            moves_done = {pid: mv for pid, mv in state.moves.items() if mv}
            if len(moves_done) < 2:
                await self.send_personal_message(
                    tg_id=tg_id,
                    message={"type": "WAIT_FOR_OPPONENT", "data": {"room_id": self.room_name}},
                )
                return

            p1, p2 = list(moves_done.keys())
            m1, m2 = moves_done[p1], moves_done[p2]

            outcome_p1 = judge(m1, m2)
            outcome_p2 = judge(m2, m1)

            if outcome_p1 == "win":
                state.scores[p1] += 1
            elif outcome_p2 == "win":
                state.scores[p2] += 1

            # персональные RESULT
            await self.send_personal_message(
                tg_id=p1,
                message={"type": "RESULT",
                         "data": {"you_move": m1, "opp_move": m2, "outcome": outcome_p1,
                                  "you_id": p1, "opp_id": p2,
                                  "scores": {"you": state.scores[p1], "opp": state.scores[p2]}}}
            )
            await self.send_personal_message(
                tg_id=p2,
                message={"type": "RESULT",
                         "data": {"you_move": m2, "opp_move": m1, "outcome": outcome_p2,
                                  "you_id": p2, "opp_id": p1,
                                  "scores": {"you": state.scores[p2], "opp": state.scores[p1]}}}
            )

            # сбрасываем ходы (след. ROUND_START придёт отдельным событием, когда оба нажмут READY — если нужно)
            state.moves[p1] = None
            state.moves[p2] = None

    async def disconnect(self, websocket: WebSocket):
        if self.room_name in self.active_connections:
            try:
                self.active_connections[self.room_name].remove(websocket)
            except ValueError:
                pass
            if not self.active_connections[self.room_name]:
                del self.active_connections[self.room_name]

    async def wait_for_opponent(self, websocket: WebSocket):
        await websocket.send_json({"type": "WAIT_FOR_OPPONENT", "data": {"room_id": self.room_name}})

    async def match_found(self, websocket: WebSocket):
        await websocket.send_json({"type": "MATCH_FOUND", "data": {"room_id": self.room_name}})

    async def find_match(self, websocket: WebSocket, t: str):
        tg_id = self._decode_tg_id(t)
        if not tg_id:
            await websocket.send_json({"type": "AUTH_FAIL", "data": {"message": "Invalid token"}})
            await websocket.close(code=4401)
            return

        self.register_player(tg_id, websocket)
        await websocket.send_json({"type": "AUTH_OK", "data": {"tg_id": tg_id}})

        if self.room_is_ready():
            await self.round_start()
        else:
            await self.wait_for_opponent(websocket)

    async def auth_player(self, websocket: WebSocket, tg_id: int):
        room_players = self.room_players.get(self.room_name, {})
        if tg_id not in room_players:
            await websocket.send_json({"type": "AUTH_FAIL", "data": {"message": "Invalid player"}})
            await websocket.close(code=4401)
            return False
        return True

    # -------------------- utils --------------------

    def _new_room_name(self) -> str:
        return f"room-{uuid.uuid4().hex[:8]}"

    def _new_round_id(self) -> str:
        return uuid.uuid4().hex

    def _decode_tg_id(self, t: str) -> int | None:
        try:
            payload = jwt.decode(t, settings.SECRET_KEY, algorithms=['HS256'])
            tg_id = int(payload.get('tg_id') or payload.get('user_id') or 0)
            return tg_id or None
        except Exception:
            return None

    async def _safe_send(self, ws: WebSocket, payload: dict):
        try:
            await ws.send_json(payload)
        except Exception:
            pass


manager = ConnectionManager()


@ws_router.websocket("/ws/rooms/")
async def websocket_rps_endpoint(websocket: WebSocket):
    """WebSocket endpoint for Rock-Paper-Scissors game room."""
    await manager.connect(websocket)

    try:
        while True:
            json_event_data = await websocket.receive_json()
            match json_event_data["type"]:
                case "JOINED":
                    tg_id = manager._decode_tg_id(json_event_data["data"].get("t", ""))
                    if not tg_id:
                        await websocket.send_json({"type": "AUTH_FAIL", "data": {"message": "Invalid token"}})
                        await websocket.close(code=4401)
                        return

                    manager.register_player(tg_id, websocket)
                    await websocket.send_json({"type": "AUTH_OK", "data": {"tg_id": tg_id}})

                    if manager.room_is_ready():
                        await manager.round_start()
                    else:
                        await manager.wait_for_opponent(websocket)

                case "MOVE":
                    player_move = Move.model_validate(json_event_data)
                    await manager.auth_player(websocket, player_move.data.tg_id)

                    if not manager.room_is_ready():
                        await manager.wait_for_opponent(websocket)
                        continue

                    await manager.receive_move(
                        tg_id=player_move.data.tg_id,
                        move=player_move.data.move
                    )

                case "READY":
                    await manager.round_start()

                case "FIND_MATCH":
                    await manager.find_match(websocket, t=json_event_data.get("t", ""))

                case _:
                    print(f"Unknown WS event type: {json_event_data['type']}")
                    print(json_event_data)
                    await websocket.send_json({"type": "ERROR", "data": {"reason": "Unknown type"}})

    except WebSocketDisconnect:
        await manager.disconnect(websocket)
