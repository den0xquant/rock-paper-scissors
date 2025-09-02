import asyncio
import json
import secrets
from dataclasses import dataclass, field

import redis.asyncio as redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from server.game.models import RoomCtx, Move, Outcome
from server.game.states import RoomState
from server.services.connection_manager import manager
from server.config import settings
from server.game.events import ClientEvent
from server.game.transitions import FSM


@dataclass
class RoomRuntime:
    ctx: RoomCtx
    fsm: FSM
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_rooms: dict[str, RoomRuntime] = {}


def _room(rt_or_id: str | RoomRuntime) -> RoomRuntime:
    if isinstance(rt_or_id, RoomRuntime):
        return rt_or_id
    room_id = rt_or_id
    rt = _rooms.get(room_id)
    if rt:
        return rt
    rt = RoomRuntime(ctx=RoomCtx(name=room_id), fsm=FSM(initial=RoomState.EMPTY))
    _rooms[room_id] = rt
    return rt


def _snapshot(rt: RoomRuntime):
    ctx = rt.ctx
    return {
        "state": rt.fsm.state.name,
        "best_of": ctx.best_of,
        "wins_needed": ctx.best_of // 2 + 1,
        "round_id": ctx.round_id,
        "players": [
            {
                "pid": p.pid,
                "state": p.state.name,
                "score": p.score,
                "last_move": p.last_move
            }
            for p in ctx.players.values()
        ],
    }


def _parse_move(v: str) -> Move:
    v = (v or "").strip().lower()
    if v in ("r", "rock"):
        return Move.ROCK
    if v in ("p", "paper"):
        return Move.PAPER
    if v in ("s", "scissors"):
        return Move.SCISSORS
    raise ValueError("move must be r/p/s")


ws_router = APIRouter(tags=["websocket"])
redis_client = redis.Redis.from_url(settings.REDIS_URI, decode_responses=True)


@ws_router.websocket("/ws/rooms/{room_id}")
async def websocket_rps_endpoint(websocket: WebSocket, room_id: str):
    """WebSocket endpoint for Rock-Paper-Scissors game room."""
    pid = websocket.query_params.get("pid") or websocket.headers.get("X-Player-Id")
    if not pid:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(room_id=room_id, pid=pid, websocket=websocket)
    rt = _room(room_id)

    try:
        async with rt.lock:
            rt.fsm.on_player_join(rt.ctx, pid)
            await manager.send_to(room_id=room_id, pid=pid, message={"type": "JOINED", "data": {"pid": pid, "room": _snapshot(rt)}})

            if rt.fsm.state in (RoomState.ONE_WAITING,):
                await manager.broadcast(room_id=room_id, message={"type": "WAITING_OPP", "data": _snapshot(rt)})
    
    except Exception as e:
        await manager.send_to(room_id=room_id, pid=pid, message={"type": "ERROR", "data": {"message": str(e)}})

    try:
        while True:
            try:
                json_data = await websocket.receive_json()
                event_type = json_data.get("type", 0)
                event_data = json_data.get("data", {})
            except json.JSONDecodeError:
                await manager.send_to(room_id=room_id, pid=pid, message={"type": "ERROR", "data": {"message": "Incorrect data."}})
                continue

            match event_type:
                case ClientEvent.START:
                    # Если состояние комнаты = EMPTY -> добавить игрока в комнату -> Переключить состояние на ONE_WAITING -> broadcast(message WAITING_OPP)
                    # Если состояние комнаты = ONE_WAITING -> добавить игрока в комнату -> Переключить состояние на ROUND_AWAIT_MOVES -> broadcast(message WAITING_MOVE)
                    
                    async with rt.lock:
                        server_evt = rt.fsm.on_start(rt.ctx)
                        if server_evt == "WAITING_MOVE":
                            await manager.broadcast(room_id=room_id, message={"type": "ROUND_START", "data": _snapshot(rt)})
                
                case ClientEvent.MOVE:
                    # Если состояние комнаты ROUND_AWAIT_MOVES -> ждать ход от одного из игроков
                    # Если походил только 1 игрок то записать ход и отправить ему WAITING_OPP
                    # Если походили 2 игрока то переключаем состояние комнаты в ROUND_RESULT сохраняем результат и broadcast(message WAITING_MOVE)
                    try:
                        mv = _parse_move(event_data.get("move"))
                    except ValueError as e:
                        await manager.send_to(room_id=room_id, pid=pid, message={"type": "ERROR", "data": {"message": f"Incorrect move: {str(e)}"}})
                        continue

                    async with rt.lock:
                        await manager.send_to(room_id=room_id, pid=pid, message={"type": "ACK_MOVE"})
                        payload = rt.fsm.on_move(rt.ctx, pid, mv)

                        if payload is None:
                            await manager.send_to(room_id=room_id, pid=pid, message={"type": "WAITING_OPP"})
                        else:
                            await manager.broadcast(room_id=room_id, message={"type": "ROUND_RESULT", "data": payload})
                            next_evt = rt.fsm.next_round_or_over(rt.ctx)
                            await manager.broadcast(room_id=room_id, message={"type": next_evt, "data": _snapshot(rt)})



    
                case _:
                    await websocket.send_text('{"type": "ERROR", "data": {"message": "Bad event type"}}')
    
    except WebSocketDisconnect:
        pass
    finally:
        pass
