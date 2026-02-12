"""Event bus for pub/sub messaging and SSE streaming."""

import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

from ..storage.models import EventCreate
from ..storage.database import db

logger = logging.getLogger(__name__)


class EventBus:
    """Pub/sub event bus for real-time updates."""

    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def emit(self, event_type: str, payload: Dict[str, Any], entity_type: str = "system", entity_id: Optional[str] = None):
        """Emit an event to all subscribers."""
        event_data = {
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Store event in database
        try:
            event = EventCreate(
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
            )
            await db.create_event(event)
        except Exception as e:
            logger.error(f"Failed to store event in database: {e}")

        # Notify all subscribers
        async with self._lock:
            # Send to wildcard subscribers
            if "*" in self._subscribers:
                for queue in self._subscribers["*"]:
                    try:
                        queue.put_nowait(event_data)
                    except asyncio.QueueFull:
                        logger.warning(f"Queue full for wildcard subscriber")

            # Send to specific event type subscribers
            if event_type in self._subscribers:
                for queue in self._subscribers[event_type]:
                    try:
                        queue.put_nowait(event_data)
                    except asyncio.QueueFull:
                        logger.warning(f"Queue full for {event_type} subscriber")

        logger.debug(f"Emitted event: {event_type}")

    async def subscribe(self, event_type: str = "*", maxsize: int = 100) -> asyncio.Queue:
        """Subscribe to events. Use '*' for all events."""
        queue = asyncio.Queue(maxsize=maxsize)

        async with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(queue)

        logger.debug(f"New subscriber for: {event_type}")
        return queue

    async def unsubscribe(self, queue: asyncio.Queue, event_type: str = "*"):
        """Unsubscribe from events."""
        async with self._lock:
            if event_type in self._subscribers:
                if queue in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(queue)
                    if not self._subscribers[event_type]:
                        del self._subscribers[event_type]

        logger.debug(f"Unsubscribed from: {event_type}")


# Global event bus instance
event_bus = EventBus()
