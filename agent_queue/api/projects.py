"""Project management API endpoints."""

from fastapi import APIRouter, HTTPException
from typing import List

from ..storage.models import Project, ProjectCreate, ProjectUpdate
from ..storage.database import db

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=List[Project])
async def list_projects():
    """List all projects."""
    return await db.list_projects()


@router.post("", response_model=Project)
async def create_project(project: ProjectCreate):
    """Register a new project."""
    existing = await db.get_project_by_name(project.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Project '{project.name}' already exists")
    return await db.create_project(project)


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: int):
    """Get a project by ID."""
    project = await db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=Project)
async def update_project(project_id: int, update: ProjectUpdate):
    """Update a project's summary, file_map, etc."""
    project = await db.update_project(project_id, update)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
