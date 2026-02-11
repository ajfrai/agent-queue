"""Session lifecycle management."""

import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

import pexpect

from ..storage.models import Session, SessionCreate, SessionUpdate, SessionStatus
from ..storage.database import db
from ..integration.claude_code_cli import claude_cli
from .event_bus import event_bus

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages Claude Code CLI session lifecycle via pexpect."""

    def __init__(self):
        self._active_sessions: Dict[int, pexpect.spawn] = {}
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

            # Spawn the CLI process via pexpect
            child = claude_cli.spawn_session(
                task_description=task_description,
                working_directory=Path(session.working_directory),
                model=session.model,
            )

            if not child:
                await self._mark_session_failed(session_id, "Failed to spawn CLI process")
                return False

            # Update session with process info
            await db.update_session(
                session_id,
                SessionUpdate(
                    status=SessionStatus.RUNNING,
                    pid=child.pid,
                    started_at=datetime.utcnow(),
                    last_heartbeat=datetime.utcnow(),
                )
            )

            # Store active session
            self._active_sessions[session_id] = child

            # Start monitoring the session
            monitor_task = asyncio.create_task(self._monitor_session(session_id))
            self._monitor_tasks[session_id] = monitor_task

            await event_bus.emit(
                "session.started",
                {
                    "session_id": session_id,
                    "pid": child.pid,
                },
                entity_type="session",
                entity_id=session.uuid,
            )

            logger.info(f"Started session {session_id} with PID {child.pid}")
            return True

        except Exception as e:
            logger.error(f"Failed to start session {session_id}: {e}")
            await self._mark_session_failed(session_id, str(e))
            return False

    async def _monitor_session(self, session_id: int):
        """Monitor a running session via pexpect."""
        try:
            session = await db.get_session(session_id)
            child = self._active_sessions.get(session_id)

            if not session or not child:
                return

            turn_count = 0

            async def output_callback(chunk: str):
                nonlocal turn_count

                await event_bus.emit(
                    "session.output",
                    {
                        "session_id": session_id,
                        "output": chunk,
                    },
                    entity_type="session",
                    entity_id=session.uuid,
                )

                if claude_cli.detect_turn_boundary(chunk):
                    turn_count += 1
                    await db.update_session(
                        session_id,
                        SessionUpdate(turn_count=turn_count)
                    )
                    await event_bus.emit(
                        "session.turn_completed",
                        {
                            "session_id": session_id,
                            "turn_count": turn_count,
                        },
                        entity_type="session",
                        entity_id=session.uuid,
                    )

            # Run the session (blocks in executor until complete)
            exit_code = await claude_cli.run_session(
                child,
                Path(session.stdout_path),
                Path(session.stderr_path),
                on_output=output_callback,
            )

            # Update session status
            status = SessionStatus.COMPLETED if exit_code == 0 else SessionStatus.FAILED
            await db.update_session(
                session_id,
                SessionUpdate(
                    status=status,
                    exit_code=exit_code,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "session.completed" if exit_code == 0 else "session.failed",
                {
                    "session_id": session_id,
                    "exit_code": exit_code,
                    "turn_count": turn_count,
                },
                entity_type="session",
                entity_id=session.uuid,
            )

            # Cleanup
            self._active_sessions.pop(session_id, None)
            self._monitor_tasks.pop(session_id, None)

            logger.info(f"Session {session_id} completed with exit code {exit_code}")

        except Exception as e:
            logger.error(f"Error monitoring session {session_id}: {e}")
            await self._mark_session_failed(session_id, str(e))

    async def cancel_session(self, session_id: int) -> bool:
        """Cancel a running session."""
        try:
            child = self._active_sessions.get(session_id)
            if not child:
                logger.warning(f"Session {session_id} is not active")
                return False

            exit_code = await claude_cli.terminate_session(child)

            await db.update_session(
                session_id,
                SessionUpdate(
                    status=SessionStatus.CANCELLED,
                    exit_code=exit_code,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "session.cancelled",
                {"session_id": session_id},
                entity_type="session",
            )

            # Cleanup
            self._active_sessions.pop(session_id, None)
            if session_id in self._monitor_tasks:
                self._monitor_tasks[session_id].cancel()
                self._monitor_tasks.pop(session_id, None)

            logger.info(f"Cancelled session {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel session {session_id}: {e}")
            return False

    async def send_message(self, session_id: int, message: str) -> bool:
        """Send a message to a running session."""
        try:
            child = self._active_sessions.get(session_id)
            if not child:
                logger.warning(f"Session {session_id} is not active")
                return False

            await claude_cli.send_input(child, message)

            await event_bus.emit(
                "session.message_sent",
                {"session_id": session_id, "message": message},
                entity_type="session",
            )

            return True

        except Exception as e:
            logger.error(f"Failed to send message to session {session_id}: {e}")
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
