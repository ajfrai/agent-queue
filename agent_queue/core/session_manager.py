"""Session lifecycle management via subprocess."""

import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from ..storage.models import Session, SessionCreate, SessionUpdate, SessionStatus
from ..storage.database import db
from ..integration.claude_code_cli import claude_cli
from .event_bus import event_bus
from .rate_limit_monitor import rate_limit_monitor

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages Claude Code CLI session lifecycle via subprocess."""

    def __init__(self):
        self._active_pids: Dict[int, int] = {}  # session_id -> pid
        self._monitor_tasks: Dict[int, asyncio.Task] = {}

    async def create_session(
        self,
        task_id: int,
        working_directory: str,
        model: str
    ) -> Optional[Session]:
        """Create a new session record."""
        try:
            session_create = SessionCreate(
                task_id=task_id,
                working_directory=working_directory,
                model=model,
            )

            session = await db.create_session(session_create)

            await event_bus.emit(
                "session.created",
                {
                    "session_id": session.id,
                    "task_id": task_id,
                    "model": model,
                },
                entity_type="session",
                entity_id=session.uuid,
            )

            logger.info(f"Created session {session.id} for task {task_id}")
            return session

        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return None

    async def start_session(self, session_id: int, task_description: str) -> bool:
        """Start a Claude Code CLI session."""
        try:
            session = await db.get_session(session_id)
            if not session:
                logger.error(f"Session {session_id} not found")
                return False

            # Update session status to running
            await db.update_session(
                session_id,
                SessionUpdate(
                    status=SessionStatus.RUNNING,
                    started_at=datetime.utcnow(),
                    last_heartbeat=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "session.started",
                {"session_id": session_id},
                entity_type="session",
                entity_id=session.uuid,
            )

            # Start monitoring the session in background
            monitor_task = asyncio.create_task(
                self._run_and_monitor_session(session_id, session, task_description)
            )
            self._monitor_tasks[session_id] = monitor_task

            logger.info(f"Started session {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to start session {session_id}: {e}")
            await self._mark_session_failed(session_id, str(e))
            return False

    async def _run_and_monitor_session(
        self, session_id: int, session: Session, task_description: str
    ):
        """Run the Claude CLI subprocess and monitor its output."""
        try:
            turn_count = 0

            async def output_callback(chunk: str):
                await event_bus.emit(
                    "session.output",
                    {
                        "session_id": session_id,
                        "output": chunk,
                    },
                    entity_type="session",
                    entity_id=session.uuid,
                )

            async def json_event_callback(event: Dict[str, Any]):
                nonlocal turn_count
                # Track turns from result events
                if event.get("type") == "result":
                    num_turns = event.get("num_turns", 0)
                    if num_turns > turn_count:
                        turn_count = num_turns
                        await db.update_session(
                            session_id,
                            SessionUpdate(turn_count=turn_count)
                        )

            # Run the task via subprocess
            result = await claude_cli.run_task(
                task_description=task_description,
                working_directory=Path(session.working_directory),
                model=session.model,
                stdout_path=Path(session.stdout_path),
                stderr_path=Path(session.stderr_path),
                on_output=output_callback,
                on_json_event=json_event_callback,
            )

            exit_code = result["exit_code"]
            pid = result.get("pid")

            if pid:
                await db.update_session(
                    session_id, SessionUpdate(pid=pid)
                )

            # Handle rate limiting
            if result.get("is_rate_limited"):
                logger.warning(f"Session {session_id} hit rate limit")
                rate_limit_monitor.mark_rate_limited()
                await event_bus.emit(
                    "session.rate_limited",
                    {
                        "session_id": session_id,
                        "rate_limit_text": result.get("rate_limit_text", ""),
                    },
                    entity_type="session",
                    entity_id=session.uuid,
                )

            # Extract Claude session ID from result
            result_json = result.get("result_json")
            claude_session_id = None
            if result_json:
                claude_session_id = result_json.get("session_id")

            # Determine final status
            if result.get("error"):
                status = SessionStatus.FAILED
            elif exit_code == 0:
                status = SessionStatus.COMPLETED
            else:
                status = SessionStatus.FAILED

            await db.update_session(
                session_id,
                SessionUpdate(
                    status=status,
                    exit_code=exit_code,
                    completed_at=datetime.utcnow(),
                )
            )

            event_type = "session.completed" if status == SessionStatus.COMPLETED else "session.failed"
            await event_bus.emit(
                event_type,
                {
                    "session_id": session_id,
                    "exit_code": exit_code,
                    "turn_count": turn_count,
                    "is_rate_limited": result.get("is_rate_limited", False),
                },
                entity_type="session",
                entity_id=session.uuid,
            )

            # Cleanup
            self._active_pids.pop(session_id, None)
            self._monitor_tasks.pop(session_id, None)

            logger.info(f"Session {session_id} finished with status={status}, exit_code={exit_code}")

        except Exception as e:
            logger.error(f"Error in session {session_id}: {e}")
            await self._mark_session_failed(session_id, str(e))
            self._active_pids.pop(session_id, None)
            self._monitor_tasks.pop(session_id, None)

    async def cancel_session(self, session_id: int) -> bool:
        """Cancel a running session."""
        try:
            pid = self._active_pids.get(session_id)
            if pid:
                await claude_cli.terminate_process(pid)

            # Cancel the monitor task
            monitor = self._monitor_tasks.get(session_id)
            if monitor:
                monitor.cancel()

            await db.update_session(
                session_id,
                SessionUpdate(
                    status=SessionStatus.CANCELLED,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "session.cancelled",
                {"session_id": session_id},
                entity_type="session",
            )

            self._active_pids.pop(session_id, None)
            self._monitor_tasks.pop(session_id, None)

            logger.info(f"Cancelled session {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel session {session_id}: {e}")
            return False

    async def _mark_session_failed(self, session_id: int, error: str):
        """Mark a session as failed."""
        try:
            await db.update_session(
                session_id,
                SessionUpdate(
                    status=SessionStatus.FAILED,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "session.failed",
                {"session_id": session_id, "error": error},
                entity_type="session",
            )

        except Exception as e:
            logger.error(f"Failed to mark session as failed: {e}")


# Global session manager instance
session_manager = SessionManager()
