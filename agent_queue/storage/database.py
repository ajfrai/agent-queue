"""Database operations for the agent queue."""

import aiosqlite
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

from ..config import config
from .models import (
    Task, TaskCreate, TaskUpdate,
    Session, SessionCreate, SessionUpdate,
    Comment, CommentCreate,
    Event, EventCreate,
    RateLimitStatus,
    Project, ProjectCreate, ProjectUpdate,
)


class Database:
    """Database operations handler."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DB_PATH

    async def init_db(self):
        """Initialize the database by running all migration files in order."""
        config.ensure_directories()

        migrations_dir = Path(__file__).parent / "migrations"
        migration_files = sorted(migrations_dir.glob("*.sql"))

        async with aiosqlite.connect(self.db_path) as conn:
            for mf in migration_files:
                with open(mf, "r") as f:
                    schema = f.read()
                await conn.executescript(schema)
            await conn.commit()

            # Add columns that may not exist yet (ALTER TABLE doesn't
            # support IF NOT EXISTS in SQLite — catch duplicates)
            for stmt in [
                "ALTER TABLE tasks ADD COLUMN project_id INTEGER REFERENCES projects(id)",
                "ALTER TABLE projects ADD COLUMN git_repo TEXT DEFAULT ''",
                "ALTER TABLE projects ADD COLUMN default_branch TEXT DEFAULT 'main'",
            ]:
                try:
                    await conn.execute(stmt)
                    await conn.commit()
                except Exception:
                    pass  # Column already exists

    # Task operations
    async def create_task(self, task: TaskCreate) -> Task:
        """Create a new task."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            # Get the next position
            cursor = await conn.execute("SELECT MAX(position) as max_pos FROM tasks")
            row = await cursor.fetchone()
            next_position = (row["max_pos"] or 0) + 1

            task_uuid = str(uuid4())
            metadata_json = json.dumps(task.metadata)

            await conn.execute(
                """
                INSERT INTO tasks (uuid, title, description, priority, position, parent_task_id, project_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_uuid, task.title, task.description, task.priority,
                 next_position, task.parent_task_id, task.project_id, metadata_json),
            )
            await conn.commit()

            cursor = await conn.execute("SELECT * FROM tasks WHERE uuid = ?", (task_uuid,))
            row = await cursor.fetchone()
            return self._row_to_task(row)

    async def get_task(self, task_id: int) -> Optional[Task]:
        """Get a task by ID."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = await cursor.fetchone()
            return self._row_to_task(row) if row else None

    async def list_tasks(
        self, status: Optional[str] = None, parent_task_id: Optional[int] = None,
        project_id: Optional[int] = None, limit: int = 100, offset: int = 0
    ) -> List[Task]:
        """List tasks with optional filtering."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status)

            if parent_task_id is not None:
                conditions.append("parent_task_id = ?")
                params.append(parent_task_id)

            if project_id is not None:
                conditions.append("project_id = ?")
                params.append(project_id)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM tasks {where} ORDER BY position, priority DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    async def get_subtasks(self, parent_id: int) -> List[Task]:
        """Get all subtasks for a parent task."""
        return await self.list_tasks(parent_task_id=parent_id)

    async def update_task(self, task_id: int, update: TaskUpdate) -> Optional[Task]:
        """Update a task. Metadata is merged, not replaced."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            updates = []
            values = []

            dump = update.model_dump(exclude_unset=True)

            # Merge metadata with existing instead of replacing
            if "metadata" in dump and dump["metadata"] is not None:
                cursor = await conn.execute(
                    "SELECT metadata FROM tasks WHERE id = ?", (task_id,)
                )
                row = await cursor.fetchone()
                if row:
                    existing = json.loads(row["metadata"]) if row["metadata"] else {}
                    existing.update(dump["metadata"])
                    dump["metadata"] = existing

            for field, value in dump.items():
                if field == "metadata":
                    updates.append(f"{field} = ?")
                    values.append(json.dumps(value))
                else:
                    updates.append(f"{field} = ?")
                    values.append(value)

            if not updates:
                return await self.get_task(task_id)

            values.append(task_id)
            query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"

            await conn.execute(query, values)
            await conn.commit()

            return await self.get_task(task_id)

    async def reorder_tasks(self, task_positions: List[Dict[str, int]]) -> bool:
        """Reorder tasks by updating their positions."""
        async with aiosqlite.connect(self.db_path) as conn:
            for item in task_positions:
                await conn.execute(
                    "UPDATE tasks SET position = ? WHERE id = ?",
                    (item["position"], item["id"]),
                )
            await conn.commit()
            return True

    async def get_next_pending_task(self) -> Optional[Task]:
        """Get the next active pending task by position.

        Only returns tasks where metadata.active is true, meaning the user
        has explicitly activated them for processing on the next heartbeat.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending' "
                "AND json_extract(metadata, '$.active') = 1 "
                "ORDER BY position, priority DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return self._row_to_task(row) if row else None

    async def get_active_unassessed_tasks(self, limit: int = 10, project_id: Optional[int] = None) -> List[Task]:
        """Get active pending tasks that haven't been assessed yet."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            query = (
                "SELECT * FROM tasks WHERE status = 'pending' "
                "AND json_extract(metadata, '$.active') = 1 "
                "AND complexity IS NULL "
            )
            params = []
            if project_id is not None:
                query += "AND project_id = ? "
                params.append(project_id)
            query += "ORDER BY position, priority DESC LIMIT ?"
            params.append(limit)
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_task(row) for row in rows]

    async def get_next_assessed_task(self, project_id: Optional[int] = None) -> Optional[Task]:
        """Get the next active pending task that has been assessed."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            query = (
                "SELECT * FROM tasks WHERE status = 'pending' "
                "AND json_extract(metadata, '$.active') = 1 "
                "AND complexity IS NOT NULL "
            )
            params = []
            if project_id is not None:
                query += "AND project_id = ? "
                params.append(project_id)
            query += "ORDER BY position, priority DESC LIMIT 1"
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return self._row_to_task(row) if row else None

    async def task_exists(self, title: str) -> bool:
        """Check if a task with this title already exists."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE title = ?", (title,)
            )
            row = await cursor.fetchone()
            return row[0] > 0

    def _row_to_task(self, row: aiosqlite.Row) -> Task:
        """Convert a database row to a Task model."""
        return Task(
            id=row["id"],
            uuid=row["uuid"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            priority=row["priority"],
            position=row["position"],
            parent_task_id=row["parent_task_id"],
            project_id=row["project_id"] if "project_id" in row.keys() else None,
            complexity=row["complexity"],
            recommended_model=row["recommended_model"],
            active_session_id=row["active_session_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    # Session operations
    async def create_session(self, session: SessionCreate) -> Session:
        """Create a new session."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            session_uuid = str(uuid4())
            artifacts_json = json.dumps(session.artifacts)

            # Create session directory
            session_dir = config.SESSIONS_DIR / session_uuid
            session_dir.mkdir(parents=True, exist_ok=True)

            stdout_path = str(session_dir / "stdout.log")
            stderr_path = str(session_dir / "stderr.log")

            await conn.execute(
                """
                INSERT INTO sessions (uuid, task_id, working_directory, model, stdout_path, stderr_path, artifacts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_uuid, session.task_id, session.working_directory, session.model,
                 stdout_path, stderr_path, artifacts_json),
            )
            await conn.commit()

            cursor = await conn.execute("SELECT * FROM sessions WHERE uuid = ?", (session_uuid,))
            row = await cursor.fetchone()
            return self._row_to_session(row)

    async def get_session(self, session_id: int) -> Optional[Session]:
        """Get a session by ID."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            return self._row_to_session(row) if row else None

    async def list_sessions(
        self, task_id: Optional[int] = None, status: Optional[str] = None
    ) -> List[Session]:
        """List sessions with optional filtering."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            conditions = []
            params = []

            if task_id:
                conditions.append("task_id = ?")
                params.append(task_id)
            if status:
                conditions.append("status = ?")
                params.append(status)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM sessions {where} ORDER BY created_at DESC"

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_session(row) for row in rows]

    async def update_session(self, session_id: int, update: SessionUpdate) -> Optional[Session]:
        """Update a session."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            updates = []
            values = []

            for field, value in update.model_dump(exclude_unset=True).items():
                if field == "artifacts":
                    updates.append(f"{field} = ?")
                    values.append(json.dumps(value))
                elif field in ["started_at", "completed_at", "last_heartbeat"]:
                    updates.append(f"{field} = ?")
                    values.append(value.isoformat() if value else None)
                else:
                    updates.append(f"{field} = ?")
                    values.append(value)

            if not updates:
                return await self.get_session(session_id)

            values.append(session_id)
            query = f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?"

            await conn.execute(query, values)
            await conn.commit()

            return await self.get_session(session_id)

    def _row_to_session(self, row: aiosqlite.Row) -> Session:
        """Convert a database row to a Session model."""
        return Session(
            id=row["id"],
            uuid=row["uuid"],
            task_id=row["task_id"],
            claude_session_id=row["claude_session_id"],
            working_directory=row["working_directory"],
            model=row["model"],
            status=row["status"],
            turn_count=row["turn_count"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            pid=row["pid"],
            exit_code=row["exit_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            last_heartbeat=datetime.fromisoformat(row["last_heartbeat"]) if row["last_heartbeat"] else None,
            artifacts=json.loads(row["artifacts"]) if row["artifacts"] else {},
        )

    # Comment operations
    async def create_comment(self, comment: CommentCreate) -> Comment:
        """Create a new comment."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            comment_uuid = str(uuid4())
            await conn.execute(
                "INSERT INTO comments (uuid, task_id, content, author) VALUES (?, ?, ?, ?)",
                (comment_uuid, comment.task_id, comment.content, comment.author),
            )
            await conn.commit()

            cursor = await conn.execute("SELECT * FROM comments WHERE uuid = ?", (comment_uuid,))
            row = await cursor.fetchone()
            return self._row_to_comment(row)

    async def list_comments(self, task_id: int) -> List[Comment]:
        """List comments for a task."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM comments WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_comment(row) for row in rows]

    def _row_to_comment(self, row: aiosqlite.Row) -> Comment:
        """Convert a database row to a Comment model."""
        return Comment(
            id=row["id"],
            uuid=row["uuid"],
            task_id=row["task_id"],
            content=row["content"],
            author=row["author"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # Event operations
    async def create_event(self, event: EventCreate) -> Event:
        """Create a new event."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            event_uuid = str(uuid4())
            payload_json = json.dumps(event.payload)

            await conn.execute(
                "INSERT INTO events (uuid, event_type, entity_type, entity_id, payload) VALUES (?, ?, ?, ?, ?)",
                (event_uuid, event.event_type, event.entity_type, event.entity_id, payload_json),
            )
            await conn.commit()

            cursor = await conn.execute("SELECT * FROM events WHERE uuid = ?", (event_uuid,))
            row = await cursor.fetchone()
            return self._row_to_event(row)

    async def list_events(
        self, event_type: Optional[str] = None, entity_id: Optional[str] = None, limit: int = 100
    ) -> List[Event]:
        """List events with optional filtering."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            conditions = []
            params = []

            if event_type:
                conditions.append("event_type = ?")
                params.append(event_type)
            if entity_id:
                conditions.append("entity_id = ?")
                params.append(entity_id)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: aiosqlite.Row) -> Event:
        """Convert a database row to an Event model."""
        return Event(
            id=row["id"],
            uuid=row["uuid"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            payload=json.loads(row["payload"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # Rate limit operations
    async def get_rate_limit_status(self) -> Optional[RateLimitStatus]:
        """Get the current rate limit status from cache."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM rate_limits WHERE id = 1")
            row = await cursor.fetchone()

            if not row:
                return None

            return RateLimitStatus(
                tier=row["tier"],
                messages_used=row["messages_used"],
                messages_limit=row["messages_limit"],
                percent_used=row["percent_used"],
                is_limited=row["percent_used"] >= 90.0,
                reset_at=datetime.fromisoformat(row["reset_at"]) if row["reset_at"] else None,
                last_updated=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            )

    async def update_rate_limit_status(self, status: Dict[str, Any]):
        """Update the rate limit status cache."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO rate_limits
                    (id, tier, messages_used, messages_limit, percent_used, reset_at, raw_output, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    status.get("tier"),
                    status.get("messages_used", 0),
                    status.get("messages_limit", 0),
                    status.get("percent_used", 0.0),
                    status.get("reset_at"),
                    status.get("raw_output"),
                ),
            )
            await conn.commit()

    # Project operations
    async def create_project(self, project: ProjectCreate) -> Project:
        """Create a new project."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            project_uuid = str(uuid4())
            await conn.execute(
                "INSERT INTO projects (uuid, name, working_directory, git_repo, summary, file_map, default_branch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_uuid, project.name, project.working_directory,
                 project.git_repo, project.summary, project.file_map,
                 project.default_branch),
            )
            await conn.commit()
            cursor = await conn.execute("SELECT * FROM projects WHERE uuid = ?", (project_uuid,))
            row = await cursor.fetchone()
            return self._row_to_project(row)

    async def get_project(self, project_id: int) -> Optional[Project]:
        """Get a project by ID."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = await cursor.fetchone()
            return self._row_to_project(row) if row else None

    async def get_project_by_name(self, name: str) -> Optional[Project]:
        """Get a project by name."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM projects WHERE name = ?", (name,))
            row = await cursor.fetchone()
            return self._row_to_project(row) if row else None

    async def list_projects(self) -> List[Project]:
        """List all projects."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM projects ORDER BY name")
            rows = await cursor.fetchall()
            return [self._row_to_project(row) for row in rows]

    async def update_project(self, project_id: int, update: ProjectUpdate) -> Optional[Project]:
        """Update a project."""
        async with aiosqlite.connect(self.db_path) as conn:
            updates = []
            values = []
            for field, value in update.model_dump(exclude_unset=True).items():
                updates.append(f"{field} = ?")
                values.append(value)
            if not updates:
                return await self.get_project(project_id)
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(project_id)
            query = f"UPDATE projects SET {', '.join(updates)} WHERE id = ?"
            await conn.execute(query, values)
            await conn.commit()
            return await self.get_project(project_id)

    def _row_to_project(self, row: aiosqlite.Row) -> Project:
        """Convert a database row to a Project model."""
        keys = row.keys()
        return Project(
            id=row["id"],
            uuid=row["uuid"],
            name=row["name"],
            working_directory=row["working_directory"],
            git_repo=row["git_repo"] if "git_repo" in keys else "",
            summary=row["summary"] or "",
            file_map=row["file_map"] or "",
            default_branch=row["default_branch"] if "default_branch" in keys else "main",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # Batch comment queries
    async def get_latest_comments(self, task_ids: List[int]) -> Dict[int, Comment]:
        """Get the most recent comment per task for a batch of task IDs."""
        if not task_ids:
            return {}
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            placeholders = ",".join("?" for _ in task_ids)
            cursor = await conn.execute(
                f"""
                SELECT c.* FROM comments c
                INNER JOIN (
                    SELECT task_id, MAX(id) as max_id
                    FROM comments
                    WHERE task_id IN ({placeholders})
                    GROUP BY task_id
                ) latest ON c.id = latest.max_id
                """,
                task_ids,
            )
            rows = await cursor.fetchall()
            return {row["task_id"]: self._row_to_comment(row) for row in rows}


# Global database instance
db = Database()
