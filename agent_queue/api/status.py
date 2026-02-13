"""System status and monitoring API endpoints."""

from fastapi import APIRouter
from datetime import datetime

from ..storage.models import SystemStatus, RateLimitStatus, TaskStatus, SessionStatus
from ..storage.database import db
from ..config import config
from ..core.heartbeat import heartbeat_manager

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=SystemStatus)
async def get_system_status():
    """Get overall system status using cached data (no blocking probes)."""
    # Use the heartbeat's cached rate status instead of triggering a new probe
    rate_limit = heartbeat_manager.last_rate_status
    if rate_limit is None:
        # Try database cache
        rate_limit = await db.get_rate_limit_status()
    if rate_limit is None:
        rate_limit = RateLimitStatus()

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


@router.get("/project")
async def get_active_project():
    """Get the active project info (if any)."""
    if config.PROJECT_ID:
        project = await db.get_project(config.PROJECT_ID)
        if project:
            return {"active": True, "name": project.name, "id": project.id, "git_repo": project.git_repo}
    return {"active": False, "name": None, "id": None, "git_repo": None}


@router.post("/heartbeat/trigger")
async def trigger_heartbeat():
    """Manually trigger a single heartbeat cycle."""
    diag = await heartbeat_manager.trigger()
    return diag
