import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import random
import time
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ValidationError
import redis.asyncio as redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from server.game.models import RoomCtx, Move
from server.game.states import RoomState
from server.services.connection_manager import manager
from server.db.redis import rds
from server.config import settings
from server.game.events import ClientEvent, ServerEvent
from server.game.transitions import FSM


@dataclass
class RoomRuntime:
    ctx: RoomCtx
    fsm: FSM
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClientMessage(BaseModel):
    type: Literal["MOVE", "READY", "PING"]
    data: dict | None = None
    meta: dict | None = None


def _hash_payload(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _round_id(rt: RoomRuntime) -> str:
    rid = getattr(rt.ctx, "round_id", None) or getattr(rt.fsm, "round_id", None)
    return str(rid) if rid is not None else f"state-{rt.fsm.state.name}"


def _idem_key(event: str, room_id: str, pid: str, rt, payload: Any | None) -> str:
    suffix = _hash_payload({"room": room_id, "pid": pid, "round": _round_id(rt), "payload": payload})
    return f"rps:idem:{event}:{suffix}"


async def _idem_seen(event: str, room_id: str, pid: str, rt, payload: Any | None, ttl_sec: int = 10) -> bool:
    key = _idem_key(event, room_id, pid, rt, payload)
    created = await rds.set(key, "1", ex=ttl_sec, nx=True)
    return not bool(created)


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
                "last_move": p.last_move if p.last_move else ""
            }
            for p in ctx.players.values()
        ],
    }


def _parse_move(v: str) -> str:
    if v in ("r", "rock"):
        return Move.ROCK.name
    if v in ("p", "paper"):
        return Move.PAPER.name
    if v in ("s", "scissors"):
        return Move.SCISSORS.name
    raise ValueError("move must be r/p/s")


def _parse_client_raw(raw: str) -> ClientMessage | None:
    try:
        return ClientMessage.model_validate(raw)
    except Exception as e:
        pass

    t = (raw or "").strip().lower()
    if t in ("3", "ready"):
        return ClientMessage(type="READY")
    if t == "ping":
        return ClientMessage(type="PING")

    try:
        mv = _parse_move(raw)
        return ClientMessage(type="MOVE", data={"move": mv})
    except Exception:
        raise ValueError("Unsupported message")


def new_cid() -> str:
    return uuid.uuid4().hex


def _evt(rt: RoomRuntime, evt, cid):
    return {
        "type": evt.name,
        "data": _snapshot(rt),
        "meta": {"cid": cid or new_cid()},
    }


def _err(msg, cid):
    return {
        "type": "ERROR",
        "data": {"message": msg},
        "meta": {"cid": cid or new_cid()},
    }


def _payload(evt, data, cid):
    return {
        "type": evt.name,
        "data": data,
        "meta": {"cid": cid or new_cid()}
    }


@asynccontextmanager
async def timed_lock(rt: RoomRuntime, op: str):
    start = time.perf_counter()
    async with rt.lock:
        try:
            yield
        finally:
            pass


async def _send_with_retry(fn, *, args, evt_type, attempts=3, base_delay=0.03):
    for i in range(attempts):
        try:
            await fn(*args)
            return
        except Exception:
            if i == attempts - 1:
                raise
            await asyncio.sleep((base_delay * (2 ** i)) + random.random() * .02)


async def _reply_to(room_id, pid, message):
    await _send_with_retry(
        manager.send_to,
        args=(room_id, pid, message),
        evt_type=message.get("type", "UNKNOWN"),
    )


async def _broadcast(room_id, message):
    await _send_with_retry(
        manager.broadcast,
        args=(room_id, message),
        evt_type=message.get("type", "UNKNOWN"),
    )


async def _push_room_hints(rt, room_id):
    st = rt.fsm.state
    cid = new_cid()
    if st in (RoomState.ONE_WAITING,):
        await _broadcast(room_id, _evt(rt, ServerEvent.WAITING_OPP, cid))
    elif st in (RoomState.ROUND_AWAIT_MOVES,):
        await _broadcast(room_id, _evt(rt, ServerEvent.WAITING_MOVE, cid))


async def _on_join(rt, room_id, pid):
    cid = new_cid()
    async with timed_lock(rt, "join"):
        rt.fsm.on_player_join(rt.ctx, pid)
    
    await _reply_to(room_id, pid, _evt(rt, ServerEvent.JOINED, cid))
    await _push_room_hints(rt, room_id)


async def _on_ready(rt, room_id, pid):
    if await _idem_seen("READY", room_id, pid, rt, payload=None, ttl_sec=10):
        await _reply_to(room_id, pid, _evt(rt, ServerEvent.ACK, new_cid()))


async def _on_move(rt, room_id, pid, move):
    event = "MOVE"
    if await _idem_seen(event, room_id, pid, rt, payload={"move": move}, ttl_sec=10):
        await _reply_to(room_id, pid, _evt(rt, ServerEvent.ACK, new_cid()))
        await _push_room_hints(rt, room_id)
        return

    await _reply_to(room_id, pid, _evt(rt, ServerEvent.ACK, new_cid()))

    async with timed_lock(rt, "move"):
        result_payload = rt.fsm.on_move(rt.ctx, pid, move)
        if result_payload is None:
            await _reply_to(room_id, pid, _evt(rt, ServerEvent.WAITING_OPP, new_cid()))
            return
        next_evt = rt.fsm.next_round_or_over(rt.cxt)
    
    await _broadcast(room_id, _payload(ServerEvent.RESULT, result_payload, new_cid()))
    await _broadcast(room_id, _evt(rt, next_evt, new_cid()))
    await _push_room_hints(rt, room_id)


async def _on_disconnect(rt, room_id: str, pid: str):
    async with timed_lock(rt, "leave"):
        rt.fsm.on_leave(rt.ctx, pid)
    await _broadcast(room_id, _evt(rt, ServerEvent.WAITING_OPP, new_cid()))


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
        await _on_join(rt, room_id, pid)
    except Exception as e:
        await _reply_to(room_id, pid, _err(str(e), new_cid()))
    
    try:
        while True:
            raw_data = await websocket.receive_text()

            if raw_data.strip().lower() == "ping":
                await _reply_to(room_id, pid, {"type": "PONG", "data": None, "meta": {"cid": new_cid()}})

            if rt.fsm.state is RoomState.MATCH_OVER:
                try:
                    as_int = int(raw_data)
                    if as_int == ClientEvent.READY.value:
                        await _on_ready(rt, room_id, pid)
                        continue
                except (TypeError, ValueError):
                    pass

            try:
                msg = _parse_client_raw(raw_data)
            except (ValueError, ValidationError) as e:
                if rt.fsm.state is RoomState.MATCH_OVER:
                    await _reply_to(room_id, pid, _err("If you are ready send '3'", new_cid()))
                else:
                    await _reply_to(room_id, pid, _err(str(e), new_cid()))
                continue

            if msg is not None:
                try:
                    if msg.type == "READY":
                        await _on_ready(rt, room_id, pid)
                    elif msg.type == "MOVE":
                        mv = (msg.data or {}).get("move")
                        print('MOVE', mv)
                        if not mv:
                            raise ValueError("MOVE requires 'data.move'")
                        await _on_move(rt, room_id, pid, mv)
                    elif msg.type == "PING":
                        await _reply_to(room_id, pid, {"type": "PONG", "data": None, "meta": {"cid": msg.meta.get("cid") if msg.meta else new_cid()}})
                    else:
                        await _reply_to(room_id, pid, _err(f"Unsupported type: {msg.type}", new_cid()))
                
                except Exception as e:
                    await _reply_to(room_id, pid, _err(str(e), new_cid()))

    except WebSocketDisconnect:
        try:
            await _on_disconnect(rt, room_id, pid)
            await manager.disconnect(room_id, pid)
        finally:
            try:
                await _push_room_hints(rt, room_id)
            except Exception as e:
                pass

    except Exception as e:
        await _reply_to(room_id, pid, _err("Internal Server Error", new_cid()))
    
    finally:
        pass
