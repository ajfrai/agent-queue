"""System status and monitoring API endpoints."""

from fastapi import APIRouter
from datetime import datetime

from ..storage.models import SystemStatus, RateLimitStatus, TaskStatus, SessionStatus
from ..storage.database import db
from ..core.rate_limit_monitor import rate_limit_monitor
from ..core.heartbeat import heartbeat_manager

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=SystemStatus)
async def get_system_status():
    """Get overall system status."""
    rate_limit = await rate_limit_monitor.get_rate_limit_status()

    all_tasks = await db.list_tasks()
    active_tasks = sum(1 for t in all_tasks if t.status in [TaskStatus.ASSESSING, TaskStatus.EXECUTING])
    pending_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.PENDING)

    all_sessions = await db.list_sessions()
    running_sessions = sum(1 for s in all_sessions if s.status == SessionStatus.RUNNING)

    return SystemStatus(
        rate_limit=rate_limit,
        active_tasks=active_tasks,
        pending_tasks=pending_tasks,
        running_sessions=running_sessions,
        heartbeat_active=heartbeat_manager.is_running(),
        last_heartbeat=heartbeat_manager.last_beat,
    )


@router.get("/heartbeat")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "heartbeat_active": heartbeat_manager.is_running(),
    }
