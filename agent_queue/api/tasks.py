"""Task management API endpoints."""

from fastapi import APIRouter, HTTPException
from typing import List, Optional

from ..storage.models import Task, TaskCreate, TaskUpdate, Comment, CommentCreate
from ..storage.database import db
from ..core.event_bus import event_bus
from ..core.task_scheduler import task_scheduler

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=List[Task])
async def list_tasks(status: Optional[str] = None, limit: int = 100, offset: int = 0):
    """List all tasks with optional filtering."""
    tasks = await db.list_tasks(status=status, limit=limit, offset=offset)
    return tasks


@router.post("", response_model=Task)
async def create_task(task: TaskCreate):
    """Create a new task."""
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


@router.get("/{task_id}/comments", response_model=List[Comment])
async def list_comments(task_id: int):
    """List comments for a task."""
    comments = await db.list_comments(task_id)
    return comments


@router.post("/{task_id}/comments", response_model=Comment)
async def create_comment(task_id: int, content: str, author: str = "user"):
    """Add a comment to a task."""
    comment = CommentCreate(task_id=task_id, content=content, author=author)
    created_comment = await db.create_comment(comment)

    await event_bus.emit(
        "comment.created",
        {
            "task_id": task_id,
            "comment_id": created_comment.id,
            "author": author,
        },
        entity_type="comment",
        entity_id=created_comment.uuid,
    )

    return created_comment
