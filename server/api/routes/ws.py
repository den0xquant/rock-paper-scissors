import asyncio
import json
from dataclasses import dataclass, field

import redis.asyncio as redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from server.game.models import RoomCtx, Move
from server.game.states import RoomState
from server.services.connection_manager import manager
from server.config import settings
from server.game.events import ClientEvent, ServerEvent
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
    print(rt.fsm.state.name)
    print(rt)
    print(ctx)
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
                "last_move": p.last_move.value if p.last_move else ""
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

    get_response_message = lambda type: {"type": type, "data": _snapshot(rt)}
    get_error_message = lambda e: {"type": "ERROR", "data": {"message": str(e)}}
    get_round_result_message = lambda type, payload: {"type": type, "data": payload}
    get_match_over_message = lambda : {"type": "ERROR", "data": {"message": "If you are ready send '3'"}}

    try:
        async with rt.lock:
            rt.fsm.on_player_join(rt.ctx, pid)
            await manager.send_to(room_id=room_id, pid=pid, message=get_response_message(ServerEvent.JOINED.name))

            if rt.fsm.state in (RoomState.ONE_WAITING,):
                await manager.broadcast(room_id=room_id, message=get_response_message(ServerEvent.WAITING_OPP.name))
    
            if rt.fsm.state in (RoomState.ROUND_AWAIT_MOVES,):
                await manager.broadcast(room_id=room_id, message=get_response_message(ServerEvent.WAITING_MOVE.name))

    except Exception as e:
        await manager.send_to(room_id=room_id, pid=pid, message=get_error_message(e))

    try:
        while True:
            str_data = await websocket.receive_text()

            if rt.fsm.state is RoomState.MATCH_OVER:
                async with rt.lock:
                    try:
                        type = int(str_data)
                    except (TypeError, ValueError) as e:
                        await manager.send_to(room_id=room_id, pid=pid, message=get_match_over_message())
                        continue
                    
                    if type == ClientEvent.READY.value:
                        next_evt = rt.fsm.on_restart(rt.ctx, pid)
                        if rt.fsm.state is RoomState.ROUND_AWAIT_MOVES:
                            await manager.broadcast(room_id=room_id, message=get_response_message(next_evt.name))
                        else:
                            await manager.send_to(room_id=room_id, pid=pid, message=get_response_message(next_evt.name))
                        continue

            try:
                mv = _parse_move(str_data)
            except ValueError as e:
                await manager.send_to(room_id=room_id, pid=pid, message=get_error_message(e))
                continue

            async with rt.lock:
                await manager.send_to(room_id=room_id, pid=pid, message=get_response_message(ServerEvent.ACK.name))
                payload = rt.fsm.on_move(rt.ctx, pid, mv)

                if payload is None:
                    await manager.send_to(room_id=room_id, pid=pid, message=get_response_message(ServerEvent.WAITING_OPP.name))
                else:
                    next_evt = rt.fsm.next_round_or_over(rt.ctx)
                    await manager.broadcast(room_id=room_id, message=get_round_result_message(ServerEvent.RESULT.name, payload))
                    await manager.broadcast(room_id=room_id, message=get_response_message(next_evt.name))

    except WebSocketDisconnect:
        rt.fsm.on_leave(rt.ctx, pid)
        await manager.disconnect(room_id=room_id, pid=pid)
        await manager.broadcast(room_id=room_id, message=get_response_message(ServerEvent.WAITING_OPP.name))

    finally:
        pass
