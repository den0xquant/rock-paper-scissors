from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # room_id -> pid -> WebSocket
        self.rooms: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, room_id: str, pid: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms.setdefault(room_id, {})[pid] = websocket

    async def disconnect(self, room_id: str, pid: str) -> None:
        room = self.rooms.get(room_id)
        if not room:
            return
        ws = room.pop(pid, None)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        if not room:
            self.rooms.pop(room_id, None)

    async def send_to(self, room_id: str, pid: str, message: dict) -> None:
        room = self.rooms.get(room_id, {})
        ws = room.get(pid)
        if not ws:
            return
        await ws.send_json(message)

    async def broadcast(self, room_id: str, message: dict) -> None:
        room = self.rooms.get(room_id, {})
        dead = []
        for pid, ws in list(room.items()):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(pid)
        
        for pid in dead:
            room.pop(pid, None)


manager = ConnectionManager()
