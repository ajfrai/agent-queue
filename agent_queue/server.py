"""FastAPI server for the agent queue."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import config
from .storage.database import db
from .storage.seed import seed_database
from .core.heartbeat import heartbeat_manager
from .api import tasks, sessions, status, events, projects

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting agent queue...")

    # Initialize database
    config.ensure_directories()
    await db.init_db()
    logger.info("Database initialized")

    # Load project if specified via CLI
    if config.PROJECT_NAME:
        project = await db.get_project_by_name(config.PROJECT_NAME)
        if project:
            config.PROJECT_ID = project.id
            config.DEFAULT_WORKING_DIR = project.working_directory
            parts = []
            if project.summary:
                parts.append(f"## Project: {project.name}\n{project.summary}")
            if project.file_map:
                parts.append(f"## File Map\n{project.file_map}")
            config.PROJECT_CONTEXT = "\n\n".join(parts)
            logger.info(f"Loaded project '{project.name}' (id={project.id})")
        else:
            logger.warning(f"Project '{config.PROJECT_NAME}' not found — running unscoped")

    # Seed default tasks
    await seed_database()

    # Start heartbeat
    await heartbeat_manager.start()
    logger.info("Heartbeat started")

    yield

    # Shutdown
    logger.info("Shutting down agent queue...")
    await heartbeat_manager.stop()
    logger.info("Heartbeat stopped")


# Create FastAPI app
app = FastAPI(
    title="Agent Queue",
    description="Autonomous task queue management for Claude Code CLI",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(tasks.router)
app.include_router(sessions.router)
app.include_router(status.router)
app.include_router(events.router)
app.include_router(projects.router)

# Serve static files (web UI)
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/")
    async def serve_index():
        """Serve the web UI."""
        return FileResponse(web_dir / "index.html")
else:
    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "name": "Agent Queue",
            "version": "0.1.0",
            "status": "running",
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agent_queue.server:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,
    )
