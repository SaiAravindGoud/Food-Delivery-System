
import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.order_connections: dict[int, set[WebSocket]] = defaultdict(set)
        self.restaurant_connections: set[WebSocket] = set()
        self.partner_connections: set[WebSocket] = set()
        self.admin_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect_order(self, order_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.order_connections[order_id].add(websocket)

    async def connect_restaurant(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.restaurant_connections.add(websocket)

    async def connect_partner(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.partner_connections.add(websocket)

    async def connect_admin(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.admin_connections.add(websocket)

    async def disconnect_order(self, order_id: int, websocket: WebSocket):
        async with self._lock:
            self.order_connections[order_id].discard(websocket)
            if not self.order_connections[order_id]:
                self.order_connections.pop(order_id, None)

    async def disconnect_restaurant(self, websocket: WebSocket):
        async with self._lock:
            self.restaurant_connections.discard(websocket)

    async def disconnect_partner(self, websocket: WebSocket):
        async with self._lock:
            self.partner_connections.discard(websocket)

    async def disconnect_admin(self, websocket: WebSocket):
        async with self._lock:
            self.admin_connections.discard(websocket)

    async def send_one(self, websocket: WebSocket, message: dict):
        try:
            await websocket.send_json(message)
        except Exception:
            logger.debug("WebSocket send failed", exc_info=True)

    async def broadcast_order(self, order_id: int, message: dict):
        connections = list(self.order_connections.get(order_id, set()))
        for websocket in connections:
            await self.send_one(websocket, message)

    async def broadcast_restaurants(self, message: dict):
        for websocket in list(self.restaurant_connections):
            await self.send_one(websocket, message)

    async def broadcast_partners(self, message: dict):
        for websocket in list(self.partner_connections):
            await self.send_one(websocket, message)

    async def broadcast_admins(self, message: dict):
        for websocket in list(self.admin_connections):
            await self.send_one(websocket, message)


manager = ConnectionManager()
