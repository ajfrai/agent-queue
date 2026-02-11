"""Task scheduling and state machine logic."""

import logging
from datetime import datetime
from pathlib import Path

from ..storage.models import Task, TaskStatus, TaskUpdate, SessionStatus
from ..storage.database import db
from ..config import config
from .event_bus import event_bus
from .assessment_engine import assessment_engine
from .session_manager import session_manager

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Manages task lifecycle and state transitions."""

    async def schedule_next_task(self) -> bool:
        """Schedule and process the next pending task.

        Returns True if a task was processed, False if no tasks available.
        """
        try:
            task = await db.get_next_pending_task()
            if not task:
                logger.debug("No pending tasks in queue")
                return False

            logger.info(f"Processing task {task.id}: {task.title}")

            if task.status == TaskStatus.PENDING:
                await self._process_pending_task(task)
            elif task.status == TaskStatus.ASSESSING:
                logger.debug(f"Task {task.id} is currently being assessed")
            elif task.status == TaskStatus.EXECUTING:
                await self._check_executing_task(task)

            return True

        except Exception as e:
            logger.error(f"Failed to schedule next task: {e}")
            return False

    async def _process_pending_task(self, task: Task):
        """Process a pending task: assess and execute."""
        try:
            # Mark as assessing
            await db.update_task(
                task.id,
                TaskUpdate(
                    status=TaskStatus.ASSESSING,
                    started_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "task.assessing",
                {"task_id": task.id, "title": task.title},
                entity_type="task",
                entity_id=task.uuid,
            )

            # Assess the task
            assessment = await assessment_engine.assess_task(task.title, task.description)

            # Update task with assessment results
            await db.update_task(
                task.id,
                TaskUpdate(
                    complexity=assessment.complexity,
                    recommended_model=assessment.recommended_model,
                    metadata={
                        "assessment": {
                            "reasoning": assessment.reasoning,
                            "subtasks": assessment.subtasks,
                        }
                    },
                )
            )

            await event_bus.emit(
                "task.assessed",
                {
                    "task_id": task.id,
                    "complexity": assessment.complexity,
                    "recommended_model": assessment.recommended_model,
                },
                entity_type="task",
                entity_id=task.uuid,
            )

            logger.info(
                f"Task {task.id} assessed: complexity={assessment.complexity}, "
                f"model={assessment.recommended_model}"
            )

            # If should decompose, keep pending for manual review
            if assessment.should_decompose:
                await db.update_task(
                    task.id,
                    TaskUpdate(status=TaskStatus.PENDING)
                )
                await event_bus.emit(
                    "task.needs_decomposition",
                    {
                        "task_id": task.id,
                        "subtasks": assessment.subtasks,
                    },
                    entity_type="task",
                    entity_id=task.uuid,
                )
                logger.info(f"Task {task.id} needs decomposition, skipping execution")
                return

            # Execute the task
            await self._execute_task(task.id, assessment.recommended_model)

        except Exception as e:
            logger.error(f"Failed to process pending task {task.id}: {e}")
            await self._mark_task_failed(task.id, str(e))

    async def _execute_task(self, task_id: int, model: str):
        """Execute a task by creating and starting a session."""
        try:
            task = await db.get_task(task_id)
            if not task:
                logger.error(f"Task {task_id} not found")
                return

            # Mark as executing
            await db.update_task(
                task_id,
                TaskUpdate(status=TaskStatus.EXECUTING)
            )

            await event_bus.emit(
                "task.executing",
                {"task_id": task_id},
                entity_type="task",
                entity_id=task.uuid,
            )

            # Create a session
            working_dir = config.DEFAULT_WORKING_DIR
            session = await session_manager.create_session(
                task_id=task_id,
                working_directory=str(working_dir),
                model=model,
            )

            if not session:
                await self._mark_task_failed(task_id, "Failed to create session")
                return

            # Update task with active session
            await db.update_task(
                task_id,
                TaskUpdate(active_session_id=session.id)
            )

            # Start the session
            success = await session_manager.start_session(
                session.id,
                f"{task.title}\n\n{task.description}"
            )

            if not success:
                await self._mark_task_failed(task_id, "Failed to start session")
                return

            logger.info(f"Task {task_id} is now executing in session {session.id}")

        except Exception as e:
            logger.error(f"Failed to execute task {task_id}: {e}")
            await self._mark_task_failed(task_id, str(e))

    async def _check_executing_task(self, task: Task):
        """Check if an executing task's session is still running."""
        try:
            if not task.active_session_id:
                logger.warning(f"Task {task.id} is executing but has no active session")
                await self._mark_task_failed(task.id, "No active session found")
                return

            session = await db.get_session(task.active_session_id)
            if not session:
                logger.warning(f"Task {task.id} session {task.active_session_id} not found")
                await self._mark_task_failed(task.id, "Session not found")
                return

            if session.status == SessionStatus.COMPLETED:
                await self._mark_task_completed(task.id, session.exit_code)
            elif session.status == SessionStatus.FAILED:
                await self._mark_task_failed(task.id, f"Session failed with exit code {session.exit_code}")
            elif session.status == SessionStatus.CANCELLED:
                await db.update_task(
                    task.id,
                    TaskUpdate(status=TaskStatus.CANCELLED)
                )
                await event_bus.emit(
                    "task.cancelled",
                    {"task_id": task.id},
                    entity_type="task",
                    entity_id=task.uuid,
                )

        except Exception as e:
            logger.error(f"Failed to check executing task {task.id}: {e}")

    async def _mark_task_completed(self, task_id: int, exit_code: int):
        """Mark a task as completed."""
        try:
            task = await db.get_task(task_id)
            if not task:
                return

            await db.update_task(
                task_id,
                TaskUpdate(
                    status=TaskStatus.COMPLETED,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "task.completed",
                {"task_id": task_id, "exit_code": exit_code},
                entity_type="task",
                entity_id=task.uuid,
            )

            logger.info(f"Task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"Failed to mark task {task_id} as completed: {e}")

    async def _mark_task_failed(self, task_id: int, error: str):
        """Mark a task as failed."""
        try:
            task = await db.get_task(task_id)
            if not task:
                return

            await db.update_task(
                task_id,
                TaskUpdate(
                    status=TaskStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    metadata={"error": error},
                )
            )

            await event_bus.emit(
                "task.failed",
                {"task_id": task_id, "error": error},
                entity_type="task",
                entity_id=task.uuid,
            )

            logger.error(f"Task {task_id} failed: {error}")

        except Exception as e:
            logger.error(f"Failed to mark task {task_id} as failed: {e}")

    async def cancel_task(self, task_id: int) -> bool:
        """Cancel a task and its active session."""
        try:
            task = await db.get_task(task_id)
            if not task:
                logger.error(f"Task {task_id} not found")
                return False

            if task.active_session_id:
                await session_manager.cancel_session(task.active_session_id)

            await db.update_task(
                task_id,
                TaskUpdate(
                    status=TaskStatus.CANCELLED,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                "task.cancelled",
                {"task_id": task_id},
                entity_type="task",
                entity_id=task.uuid,
            )

            logger.info(f"Cancelled task {task_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel task {task_id}: {e}")
            return False


# Global task scheduler instance
task_scheduler = TaskScheduler()
