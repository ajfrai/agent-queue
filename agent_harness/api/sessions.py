"""Session management API endpoints."""

import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pathlib import Path

from ..storage.models import Session
from ..storage.database import db
from ..core.session_manager import session_manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: int):
    """Get session details."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/{session_id}/output")
async def stream_session_output(session_id: int):
    """Stream session output (stdout)."""
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.stdout_path:
        raise HTTPException(status_code=404, detail="No output file found")

    stdout_path = Path(session.stdout_path)
    if not stdout_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    async def generate():
        with open(stdout_path, "r") as f:
            while True:
                line = f.readline()
                if line:
                    yield line
                else:
                    current_session = await db.get_session(session_id)
                    if current_session.status in ["completed", "failed", "cancelled"]:
                        break
                    await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/plain")


@router.post("/{session_id}/cancel")
async def cancel_session(session_id: int):
    """Cancel a running session."""
    success = await session_manager.cancel_session(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to cancel session")
    return {"status": "cancelled"}


@router.post("/{session_id}/message")
async def send_message(session_id: int, message: str):
    """Send a message to a running session."""
    success = await session_manager.send_message(session_id, message)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to send message")
    return {"status": "sent"}
