"""Task management API endpoints."""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict

from ..storage.models import Task, TaskCreate, TaskUpdate, TaskStatus, Comment, CommentCreate, Event
from ..storage.database import db
from ..config import config
from ..core.event_bus import event_bus
from ..core.task_scheduler import task_scheduler

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=List[Task])
async def list_tasks(status: Optional[str] = None, limit: int = 100, offset: int = 0):
    """List all tasks with optional filtering. Scoped to active project if set."""
    tasks = await db.list_tasks(
        status=status, project_id=config.PROJECT_ID, limit=limit, offset=offset
    )
    return tasks


@router.post("", response_model=Task)
async def create_task(task: TaskCreate):
    """Create a new task."""
    # Auto-assign project_id if not set and a project is active
    if task.project_id is None and config.PROJECT_ID is not None:
        task.project_id = config.PROJECT_ID

    created_task = await db.create_task(task)

    await event_bus.emit(
        "task.created",
        {
            "task_id": created_task.id,
            "title": created_task.title,
            "priority": created_task.priority,
        },
        entity_type="task",
        entity_id=created_task.uuid,
    )

    return created_task


# This must be declared before /{task_id} to avoid path conflict
@router.get("/latest-comments")
async def get_latest_comments(task_ids: str = Query(..., description="Comma-separated task IDs")):
    """Get the latest comment per task for a batch of task IDs."""
    try:
        ids = [int(x.strip()) for x in task_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="task_ids must be comma-separated integers")
    comments = await db.get_latest_comments(ids)
    return {str(tid): comment.model_dump(mode="json") for tid, comment in comments.items()}


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: int):
    """Get a specific task."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=Task)
async def update_task(task_id: int, update: TaskUpdate):
    """Update a task."""
    task = await db.update_task(task_id, update)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await event_bus.emit(
        "task.updated",
        {
            "task_id": task_id,
            "updates": update.model_dump(exclude_unset=True),
        },
        entity_type="task",
        entity_id=task.uuid,
    )

    return task


class StatusChangeRequest(BaseModel):
    status: str


VALID_STATUSES = {
    TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.DECOMPOSED,
    TaskStatus.READY_FOR_REVIEW, TaskStatus.COMPLETED,
    TaskStatus.FAILED, TaskStatus.CANCELLED,
}


@router.post("/{task_id}/status", response_model=Task)
async def change_task_status(task_id: int, body: StatusChangeRequest):
    """Manually change a task's status with proper side effects."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    new_status = body.status
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    update_fields: dict = {"status": new_status}

    # Set completed_at for terminal states
    if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        update_fields["completed_at"] = datetime.utcnow()

    # Clear completed_at if moving back to non-terminal
    if new_status in (TaskStatus.PENDING, TaskStatus.EXECUTING):
        update_fields["completed_at"] = None

    updated = await db.update_task(task_id, TaskUpdate(**update_fields))

    await event_bus.emit(
        f"task.{new_status}",
        {"task_id": task_id, "manual": True, "previous_status": task.status},
        entity_type="task",
        entity_id=task.uuid,
    )

    # Check parent auto-completion when marking a subtask terminal
    if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.READY_FOR_REVIEW):
        if task.parent_task_id:
            await task_scheduler._check_parent_completion(task.parent_task_id)

    return updated


@router.delete("/{task_id}")
async def cancel_task(task_id: int):
    """Cancel a task."""
    success = await task_scheduler.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or already completed")

    return {"status": "cancelled"}


@router.post("/reorder")
async def reorder_tasks(task_positions: List[dict]):
    """Reorder tasks (for drag-and-drop)."""
    success = await db.reorder_tasks(task_positions)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to reorder tasks")

    await event_bus.emit(
        "tasks.reordered",
        {"positions": task_positions},
        entity_type="system",
    )

    return {"status": "reordered"}


@router.get("/{task_id}/subtasks", response_model=List[Task])
async def list_subtasks(task_id: int):
    """List subtasks for a parent task."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    subtasks = await db.get_subtasks(task_id)
    return subtasks


@router.get("/{task_id}/events", response_model=List[Event])
async def list_task_events(task_id: int):
    """List events for a task."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = await db.list_events(entity_id=task.uuid)
    return events


@router.get("/{task_id}/comments", response_model=List[Comment])
async def list_comments(task_id: int):
    """List comments for a task."""
    comments = await db.list_comments(task_id)
    return comments


class CommentBody(BaseModel):
    content: str
    author: str = "user"


@router.post("/{task_id}/comments", response_model=Comment)
async def create_comment(task_id: int, body: CommentBody):
    """Add a comment to a task.

    If the task is ready_for_review and the comment is from a user,
    automatically send it back to pending so the scheduler re-runs it
    with the feedback.
    """
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    comment = CommentCreate(task_id=task_id, content=body.content, author=body.author)
    created_comment = await db.create_comment(comment)

    await event_bus.emit(
        "comment.created",
        {
            "task_id": task_id,
            "comment_id": created_comment.id,
            "author": body.author,
        },
        entity_type="comment",
        entity_id=created_comment.uuid,
    )

    # Auto-requeue: user feedback on a reviewed task sends it back for rework
    if body.author == "user" and task.status == TaskStatus.READY_FOR_REVIEW:
        await db.update_task(
            task_id,
            TaskUpdate(
                status=TaskStatus.PENDING,
                active_session_id=None,
                completed_at=None,
            ),
        )
        await event_bus.emit(
            "task.requeued",
            {"task_id": task_id, "reason": "user_feedback"},
            entity_type="task",
            entity_id=task.uuid,
        )

    return created_comment
