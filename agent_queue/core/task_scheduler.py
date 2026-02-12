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

    async def dedupe_tasks(self) -> int:
        """Remove duplicate pending tasks, keeping the one with the lowest position.

        Matches on normalized (lowercased, stripped) title.
        Returns number of duplicates cancelled.
        """
        try:
            all_tasks = await db.list_tasks(status=TaskStatus.PENDING)
            seen: dict[str, Task] = {}
            dupes = []

            for task in sorted(all_tasks, key=lambda t: t.position):
                key = task.title.strip().lower()
                if key in seen:
                    dupes.append(task)
                else:
                    seen[key] = task

            for task in dupes:
                await db.update_task(
                    task.id,
                    TaskUpdate(
                        status=TaskStatus.CANCELLED,
                        completed_at=datetime.utcnow(),
                        metadata={"cancelled_reason": "duplicate"},
                    )
                )
                await event_bus.emit(
                    "task.cancelled",
                    {"task_id": task.id, "reason": "duplicate"},
                    entity_type="task",
                    entity_id=task.uuid,
                )
                logger.info(f"Cancelled duplicate task {task.id}: {task.title}")

            return len(dupes)

        except Exception as e:
            logger.error(f"Failed to dedupe tasks: {e}")
            return 0

    # --- Comment phase ---

    async def comment_on_tasks(self) -> int:
        """Review active tasks and leave comments where useful.

        Returns number of comments left.
        """
        try:
            # Get all active pending tasks (assessed or not)
            all_pending = await db.list_tasks(status=TaskStatus.PENDING)
            active_tasks = [
                t for t in all_pending
                if t.metadata and t.metadata.get("active")
            ]

            if not active_tasks:
                logger.debug("No active tasks to review")
                return 0

            # Build review input with status context
            review_input = []
            for t in active_tasks[:10]:
                info_parts = []
                if t.complexity:
                    info_parts.append(f"assessed: {t.complexity}/{t.recommended_model}")
                else:
                    info_parts.append("not yet assessed")
                if t.parent_task_id:
                    info_parts.append(f"subtask of #{t.parent_task_id}")
                retry = (t.metadata or {}).get("retry_count", 0)
                if retry:
                    info_parts.append(f"retried {retry}x")
                review_input.append((t.id, t.title, t.description, ", ".join(info_parts)))

            comments = await assessment_engine.review_tasks(review_input)

            if not comments:
                logger.debug("Model had no comments")
                return 0

            from ..storage.models import CommentCreate
            for c in comments:
                task = next((t for t in active_tasks if t.id == c["id"]), None)
                if not task:
                    continue

                await db.create_comment(CommentCreate(
                    task_id=c["id"],
                    content=c["comment"],
                    author="system",
                ))

                await event_bus.emit(
                    "comment.created",
                    {"task_id": c["id"], "author": "system", "comment": c["comment"]},
                    entity_type="task",
                    entity_id=task.uuid,
                )

                logger.info(f"Comment on task {c['id']}: {c['comment'][:80]}")

            return len(comments)

        except Exception as e:
            logger.error(f"Failed to comment on tasks: {e}")
            return 0

    # --- Assess phase ---

    async def assess_pending_tasks(self) -> int:
        """Assess up to 10 active pending tasks that haven't been assessed yet.

        Returns number of tasks assessed.
        """
        try:
            tasks = await db.get_active_unassessed_tasks(limit=10)
            if not tasks:
                logger.debug("No unassessed tasks to process")
                return 0

            logger.info(f"Assessing batch of {len(tasks)} tasks")

            # Build batch input
            batch = [(t.id, t.title, t.description) for t in tasks]
            results = await assessment_engine.assess_batch(batch)

            assessed = 0
            for task in tasks:
                result = results.get(task.id)
                if not result:
                    continue

                # Update task with assessment — stays pending
                await db.update_task(
                    task.id,
                    TaskUpdate(
                        complexity=result.complexity,
                        recommended_model=result.recommended_model,
                        metadata={
                            "assessment": {
                                "reasoning": result.reasoning,
                                "subtasks": result.subtasks,
                                "should_decompose": result.should_decompose,
                            }
                        },
                    )
                )

                await event_bus.emit(
                    "task.assessed",
                    {
                        "task_id": task.id,
                        "complexity": result.complexity,
                        "recommended_model": result.recommended_model,
                    },
                    entity_type="task",
                    entity_id=task.uuid,
                )

                logger.info(
                    f"Task {task.id} assessed: complexity={result.complexity}, "
                    f"model={result.recommended_model}"
                )
                assessed += 1

            return assessed

        except Exception as e:
            logger.error(f"Failed to assess pending tasks: {e}")
            return 0

    # --- Even heartbeat: execute one task ---

    async def execute_next_task(self) -> bool:
        """Pick the next assessed+active pending task and execute it.

        Also checks on any currently executing tasks.
        Returns True if a task was acted on.
        """
        try:
            # First check executing tasks
            executing_tasks = await db.list_tasks(status=TaskStatus.EXECUTING)
            for task in executing_tasks:
                await self._check_executing_task(task)

            # Find next assessed task to execute
            task = await db.get_next_assessed_task()
            if not task:
                logger.debug("No assessed tasks ready to execute")
                return len(executing_tasks) > 0

            # Check if user flagged for decomposition
            force_decompose = (
                task.metadata
                and task.metadata.get("decompose_on_heartbeat", False)
            )
            assessment = task.metadata.get("assessment", {})
            should_decompose = assessment.get("should_decompose", False)

            if should_decompose or force_decompose:
                await self._decompose_task(task, assessment.get("subtasks", []))
                return True

            # Execute the task
            model = task.recommended_model or "sonnet"
            await self._execute_task(task.id, model)
            return True

        except Exception as e:
            logger.error(f"Failed to execute next task: {e}")
            return False

    async def _decompose_task(self, task: Task, subtask_titles: list):
        """Decompose a task into subtasks."""
        try:
            from ..storage.models import TaskCreate
            all_tasks = await db.list_tasks()
            min_position = min((t.position for t in all_tasks), default=1)

            created_ids = []
            for i, subtask_title in enumerate(subtask_titles):
                child = await db.create_task(TaskCreate(
                    title=subtask_title,
                    description=f"Subtask of: {task.title}",
                    priority=task.priority,
                    parent_task_id=task.id,
                    metadata={"active": True},
                ))
                await db.update_task(child.id, TaskUpdate(
                    position=min_position - len(subtask_titles) + i
                ))
                created_ids.append(child.id)
                await event_bus.emit(
                    "task.created",
                    {"task_id": child.id, "title": child.title, "parent_task_id": task.id},
                    entity_type="task",
                    entity_id=child.uuid,
                )

            await db.update_task(
                task.id,
                TaskUpdate(
                    status=TaskStatus.DECOMPOSED,
                    metadata={
                        "decompose_on_heartbeat": False,
                        "decomposed_into": created_ids,
                    },
                )
            )

            await event_bus.emit(
                "task.needs_decomposition",
                {
                    "task_id": task.id,
                    "subtasks": subtask_titles,
                    "created_task_ids": created_ids,
                },
                entity_type="task",
                entity_id=task.uuid,
            )
            logger.info(
                f"Task {task.id} decomposed into {len(created_ids)} subtasks: {created_ids}"
            )

        except Exception as e:
            logger.error(f"Failed to decompose task {task.id}: {e}")
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

            # Check if parent should auto-complete
            if task.parent_task_id:
                await self._check_parent_completion(task.parent_task_id)

        except Exception as e:
            logger.error(f"Failed to mark task {task_id} as completed: {e}")

    async def _mark_task_failed(self, task_id: int, error: str):
        """Mark a task as failed and requeue it for retry."""
        try:
            task = await db.get_task(task_id)
            if not task:
                return

            # Requeue: reset to pending so it can be retried
            retry_count = (task.metadata or {}).get("retry_count", 0) + 1
            await db.update_task(
                task_id,
                TaskUpdate(
                    status=TaskStatus.PENDING,
                    started_at=None,
                    completed_at=None,
                    active_session_id=None,
                    metadata={
                        "error": error,
                        "retry_count": retry_count,
                        "last_failure": datetime.utcnow().isoformat(),
                    },
                )
            )

            await event_bus.emit(
                "task.requeued",
                {"task_id": task_id, "error": error, "retry_count": retry_count},
                entity_type="task",
                entity_id=task.uuid,
            )

            logger.warning(f"Task {task_id} failed and requeued (retry #{retry_count}): {error}")

        except Exception as e:
            logger.error(f"Failed to requeue task {task_id}: {e}")

    async def _check_parent_completion(self, parent_id: int):
        """Auto-complete a decomposed parent when all subtasks reach terminal state."""
        try:
            parent = await db.get_task(parent_id)
            if not parent or parent.status != TaskStatus.DECOMPOSED:
                return

            subtasks = await db.get_subtasks(parent_id)
            if not subtasks:
                return

            terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            if not all(s.status in terminal for s in subtasks):
                return

            # All subtasks done — mark parent based on subtask outcomes
            any_failed = any(s.status == TaskStatus.FAILED for s in subtasks)
            new_status = TaskStatus.FAILED if any_failed else TaskStatus.COMPLETED

            await db.update_task(
                parent_id,
                TaskUpdate(
                    status=new_status,
                    completed_at=datetime.utcnow(),
                )
            )

            await event_bus.emit(
                f"task.{new_status}",
                {"task_id": parent_id, "auto_completed": True},
                entity_type="task",
                entity_id=parent.uuid,
            )

            logger.info(f"Parent task {parent_id} auto-completed with status: {new_status}")

        except Exception as e:
            logger.error(f"Failed to check parent completion for {parent_id}: {e}")

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
