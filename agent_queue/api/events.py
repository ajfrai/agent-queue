"""SSE event streaming API endpoints."""

import asyncio
import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..core.event_bus import event_bus

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/stream")
async def stream_events():
    """Stream all events via Server-Sent Events (SSE)."""

    async def event_generator():
        # Subscribe to all events
        queue = await event_bus.subscribe("*", maxsize=1000)

        try:
            while True:
                # Get next event from queue
                event = await queue.get()

                # Format as SSE event
                yield {
                    "event": event["event_type"],
                    "data": json.dumps(event),
                }

        except asyncio.CancelledError:
            # Cleanup on disconnect
            await event_bus.unsubscribe(queue, "*")
            raise

    return EventSourceResponse(event_generator())
