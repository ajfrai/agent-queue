"""Pydantic models for the agent queue."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class TaskStatus:
    PENDING = "pending"
    ASSESSING = "assessing"
    EXECUTING = "executing"
    DECOMPOSED = "decomposed"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Complexity:
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class TaskCreate(BaseModel):
    title: str
    description: str
    priority: int = 0
    parent_task_id: Optional[int] = None
    project_id: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    position: Optional[int] = None
    parent_task_id: Optional[int] = None
    complexity: Optional[str] = None
    recommended_model: Optional[str] = None
    active_session_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class Task(BaseModel):
    id: int
    uuid: str
    title: str
    description: str
    status: str
    priority: int
    position: int
    parent_task_id: Optional[int] = None
    project_id: Optional[int] = None
    complexity: Optional[str] = None
    recommended_model: Optional[str] = None
    active_session_id: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True


# Session models
class SessionStatus:
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionCreate(BaseModel):
    task_id: int
    working_directory: str
    model: str
    artifacts: Dict[str, Any] = Field(default_factory=dict)


class SessionUpdate(BaseModel):
    status: Optional[str] = None
    turn_count: Optional[int] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    artifacts: Optional[Dict[str, Any]] = None


class Session(BaseModel):
    id: int
    uuid: str
    task_id: int
    claude_session_id: Optional[str] = None
    working_directory: str
    model: str
    status: str
    turn_count: int
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    artifacts: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True


# Comment models
class CommentCreate(BaseModel):
    task_id: int
    content: str
    author: str = "user"


class Comment(BaseModel):
    id: int
    uuid: str
    task_id: int
    content: str
    author: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Event models
class EventCreate(BaseModel):
    event_type: str
    entity_type: str
    entity_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    id: int
    uuid: str
    event_type: str
    entity_type: str
    entity_id: Optional[str] = None
    payload: Dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


# Rate limit models
class RateLimitStatus(BaseModel):
    tier: Optional[str] = None
    messages_used: int = 0
    messages_limit: int = 0
    percent_used: float = 0.0
    is_limited: bool = False
    reset_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None


# Assessment result models
class AssessmentResult(BaseModel):
    complexity: str  # simple, medium, complex
    recommended_model: str  # haiku, sonnet, opus
    should_decompose: bool = False
    subtasks: List[str] = Field(default_factory=list)
    reasoning: str = ""
    comment: Optional[str] = None


# System status models
class SystemStatus(BaseModel):
    rate_limit: RateLimitStatus
    active_tasks: int
    pending_tasks: int
    running_sessions: int
    heartbeat_active: bool
    last_heartbeat: Optional[datetime] = None


# Project models
class ProjectCreate(BaseModel):
    name: str
    working_directory: str
    git_repo: str = ""
    summary: str = ""
    file_map: str = ""
    default_branch: str = "main"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    working_directory: Optional[str] = None
    git_repo: Optional[str] = None
    summary: Optional[str] = None
    file_map: Optional[str] = None
    default_branch: Optional[str] = None


class Project(BaseModel):
    id: int
    uuid: str
    name: str
    working_directory: str
    git_repo: str
    summary: str
    file_map: str
    default_branch: str = "main"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
