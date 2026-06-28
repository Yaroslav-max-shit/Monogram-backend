from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict
import asyncio

router = APIRouter(prefix="/calls", tags=["calls"])

class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, Dict[int, WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def join(self, room_id: str, user_id: int, ws: WebSocket, chat_members: set = None):
        if chat_members is not None and user_id not in chat_members:
            await ws.close(code=4003)
            return
        await ws.accept()
        async with self._lock:
            if room_id not in self.rooms:
                self.rooms[room_id] = {}
            self.rooms[room_id][user_id] = ws
            for uid, uws in self.rooms[room_id].items():
                if uid != user_id:
                    try:
                        await uws.send_json({"type": "peer_joined", "user_id": user_id})
                    except Exception:
                        pass

    async def leave(self, room_id: str, user_id: int):
        async with self._lock:
            if room_id in self.rooms:
                self.rooms[room_id].pop(user_id, None)
                for uid, uws in self.rooms[room_id].items():
                    try:
                        await uws.send_json({"type": "peer_left", "user_id": user_id})
                    except Exception:
                        pass
                if not self.rooms[room_id]:
                    del self.rooms[room_id]

    async def relay(self, room_id: str, sender_id: int, data: dict):
        async with self._lock:
            if room_id in self.rooms:
                for uid, uws in self.rooms[room_id].items():
                    if uid != sender_id:
                        try:
                            await uws.send_json(data)
                        except Exception:
                            pass

room_manager = RoomManager()

@router.websocket("/room/{room_id}/{user_id}")
async def call_websocket(room_id: str, user_id: int, ws: WebSocket):
    from database import SessionLocal
    from models import Membership
    db = SessionLocal()
    try:
        members = db.query(Membership).filter(Membership.chat_id == int(room_id)).all()
        chat_members = {m.user_id for m in members}
    except Exception:
        chat_members = None
    finally:
        db.close()
    await room_manager.join(room_id, user_id, ws, chat_members)
    try:
        while True:
            data = await ws.receive_json()
            data["sender_id"] = user_id
            await room_manager.relay(room_id, user_id, data)
    except WebSocketDisconnect:
        await room_manager.leave(room_id, user_id)
    except Exception:
        await room_manager.leave(room_id, user_id)
