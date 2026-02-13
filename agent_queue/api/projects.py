"""Project management API endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ..storage.models import Project, ProjectCreate, ProjectUpdate
from ..storage.database import db
from ..core.event_bus import event_bus
from ..core import git_manager

router = APIRouter(prefix="/api/projects", tags=["projects"])


class SwitchRequest(BaseModel):
    project_id: Optional[int] = None


class ImportRequest(BaseModel):
    git_repo: str  # "owner/repo" format


class NewProjectRequest(BaseModel):
    name: str
    private: bool = False


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


@router.post("/{project_id}/sync")
async def sync_project(project_id: int):
    """Sync a project's local clone with GitHub (pull + re-detect branch)."""
    project = await db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    working_dir = Path(project.working_directory)
    if not working_dir.exists():
        if not project.git_repo:
            raise HTTPException(status_code=400, detail="No git_repo and working directory missing")
        # Re-clone
        try:
            working_dir = await git_manager.clone_repo(project.git_repo)
            await db.update_project(project_id, ProjectUpdate(
                working_directory=str(working_dir),
            ))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=f"Re-clone failed: {e}")

    # Pull latest
    rc, out, err = await git_manager._run(["git", "pull", "--ff-only"], cwd=working_dir)
    pull_status = "ok" if rc == 0 else f"pull failed: {err}"

    # Re-detect default branch
    default_branch = await git_manager.get_default_branch(working_dir)
    if default_branch != project.default_branch:
        await db.update_project(project_id, ProjectUpdate(default_branch=default_branch))

    updated = await db.get_project(project_id)
    return {
        "ok": True,
        "pull": pull_status,
        "default_branch": default_branch,
        "project": updated,
    }


@router.post("/switch")
async def switch_project(req: SwitchRequest):
    """Switch the active project (or unscope with project_id=null)."""
    from ..server import load_project

    project = await load_project(req.project_id)

    if req.project_id is not None and project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    await event_bus.emit(
        "project.switched",
        {
            "project_id": req.project_id,
            "project_name": project.name if project else None,
        },
        entity_type="project",
        entity_id=project.uuid if project else None,
    )

    return {
        "ok": True,
        "project_id": req.project_id,
        "project_name": project.name if project else None,
    }


@router.post("/new", response_model=Project)
async def new_project(req: NewProjectRequest):
    """Create a brand-new project: creates a GitHub repo, clones it locally, and registers it."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")

    existing = await db.get_project_by_name(name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Project '{name}' already exists")

    try:
        owner_repo, local_path = await git_manager.create_repo(name, private=req.private)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    default_branch = await git_manager.get_default_branch(local_path)

    project = await db.create_project(ProjectCreate(
        name=name,
        working_directory=str(local_path),
        git_repo=owner_repo,
        default_branch=default_branch,
    ))

    await event_bus.emit(
        "project.created",
        {"project_id": project.id, "name": project.name, "git_repo": owner_repo},
        entity_type="project",
        entity_id=project.uuid,
    )

    return project


@router.post("/import", response_model=Project)
async def import_repo(req: ImportRequest):
    """Import a GitHub repository: clone it and create a project."""
    # Validate format
    parts = req.git_repo.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(status_code=400, detail="git_repo must be in 'owner/repo' format")

    repo_name = parts[1]

    # Check if project already exists
    existing = await db.get_project_by_name(repo_name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{repo_name}' already exists (id={existing.id})",
        )

    # Clone the repo
    try:
        clone_path = await git_manager.clone_repo(req.git_repo)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Detect default branch
    default_branch = await git_manager.get_default_branch(clone_path)

    # Create the project
    project = await db.create_project(ProjectCreate(
        name=repo_name,
        working_directory=str(clone_path),
        git_repo=req.git_repo,
        default_branch=default_branch,
    ))

    await event_bus.emit(
        "project.created",
        {"project_id": project.id, "name": project.name, "git_repo": req.git_repo},
        entity_type="project",
        entity_id=project.uuid,
    )

    return project
