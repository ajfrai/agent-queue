"""Task scheduling and state machine logic."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from ..storage.models import Task, TaskStatus, TaskUpdate, SessionStatus
from ..storage.database import db
from ..config import config
from .event_bus import event_bus
from .assessment_engine import assessment_engine
from .session_manager import session_manager
from . import git_manager

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

    # --- Assess phase (with integrated comments) ---

    async def assess_pending_tasks(self) -> int:
        """Assess up to 10 active pending tasks that haven't been assessed yet.

        Assessment now includes an optional comment field — when the model
        has something useful to say, it returns a comment alongside the
        assessment, eliminating the need for a separate comment phase.

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

                # Create comment if the model had something useful to say
                if result.comment:
                    from ..storage.models import CommentCreate
                    await db.create_comment(CommentCreate(
                        task_id=task.id,
                        content=result.comment,
                        author="system",
                    ))
                    await event_bus.emit(
                        "comment.created",
                        {"task_id": task.id, "author": "system", "comment": result.comment},
                        entity_type="task",
                        entity_id=task.uuid,
                    )
                    logger.info(f"Assessment comment on task {task.id}: {result.comment[:80]}")

                logger.info(
                    f"Task {task.id} assessed: complexity={result.complexity}, "
                    f"model={result.recommended_model}"
                )
                assessed += 1

            return assessed

        except Exception as e:
            logger.error(f"Failed to assess pending tasks: {e}")
            return 0

    # --- Even heartbeat: execute tasks in parallel ---

    async def execute_next_tasks(self) -> int:
        """Pick assessed+active pending tasks and execute them in parallel.

        Also checks on any currently executing tasks.
        Returns count of tasks acted on.
        """
        try:
            # First check executing tasks
            executing_tasks = await db.list_tasks(status=TaskStatus.EXECUTING)
            for task in executing_tasks:
                await self._check_executing_task(task)

            # Calculate available slots
            # Re-fetch since _check_executing_task may have changed statuses
            still_executing = await db.list_tasks(status=TaskStatus.EXECUTING)
            available_slots = config.MAX_CONCURRENT_TASKS - len(still_executing)

            if available_slots <= 0:
                logger.debug(f"All {config.MAX_CONCURRENT_TASKS} execution slots occupied")
                return len(still_executing)

            # Find next assessed tasks to fill available slots
            tasks = await db.get_next_assessed_tasks(limit=available_slots)
            if not tasks:
                logger.debug("No assessed tasks ready to execute")
                return len(still_executing)

            acted = 0
            launch_coros = []
            for task in tasks:
                # Check if user flagged for decomposition
                force_decompose = (
                    task.metadata
                    and task.metadata.get("decompose_on_heartbeat", False)
                )
                assessment = task.metadata.get("assessment", {})
                should_decompose = assessment.get("should_decompose", False)

                if should_decompose or force_decompose:
                    await self._decompose_task(task, assessment.get("subtasks", []))
                    acted += 1
                else:
                    model = task.recommended_model or "sonnet"
                    launch_coros.append(self._execute_task(task.id, model))

            # Launch remaining tasks in parallel
            if launch_coros:
                await asyncio.gather(*launch_coros, return_exceptions=True)
                acted += len(launch_coros)

            return acted + len(still_executing)

        except Exception as e:
            logger.error(f"Failed to execute next tasks: {e}")
            return 0

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
        """Execute a task by creating an isolated worktree and starting a session."""
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

            # Create isolated worktree if project has a git repo
            working_dir = config.DEFAULT_WORKING_DIR
            worktree_path = None
            repo_dir = None
            if task.project_id:
                project = await db.get_project(task.project_id)
                if project and project.git_repo:
                    repo_dir = Path(project.working_directory)
                    slug = git_manager.slugify(task.title)
                    branch_name = f"task-{task_id}-{slug}"
                    try:
                        worktree_path = await git_manager.create_worktree(repo_dir, branch_name)
                        working_dir = worktree_path
                        await db.update_task(
                            task_id,
                            TaskUpdate(metadata={
                                "branch": branch_name,
                                "worktree_path": str(worktree_path),
                                "repo_dir": str(repo_dir),
                            })
                        )
                        logger.info(f"Created worktree at {worktree_path} for task {task_id}")
                    except Exception as e:
                        logger.warning(f"Failed to create worktree for task {task_id}: {e}")
                        # Fall back to project working directory
                        working_dir = repo_dir

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

            # Build session prompt — no PROJECT_CONTEXT injection.
            # Claude Code reads CLAUDE.md from the working directory automatically.
            prompt_parts = []
            prompt_parts.append(task.title)
            prompt_parts.append(task.description)

            # Include comment history so Claude sees reviewer feedback
            comments = await db.list_comments(task_id)
            if comments:
                prompt_parts.append("---\n## Comment history")
                for c in comments:
                    prompt_parts.append(f"[{c.author}]: {c.content}")
                prompt_parts.append(
                    "\nThis task was previously attempted. A reviewer sent it back. "
                    "Address the feedback in the comments above, then continue."
                )

            # Git/PR instructions — prevent Claude from doing harness work
            prompt_parts.append(
                "---\n"
                "## Git rules\n"
                "You are already on a dedicated branch in an isolated worktree. "
                "Do NOT run git checkout, git branch, git commit, git push, "
                "gh pr create, or any other git/gh commands. "
                "The harness that launched you handles all git operations — "
                "branching, committing, pushing, and PR creation happen automatically "
                "after your session ends. Just write code, edit files, and run tests."
            )

            prompt_parts.append(
                "---\n"
                "IMPORTANT: When you finish, end your response with a section titled "
                "'## How to test' that explains step-by-step how to verify your changes work. "
                "Include specific commands to run, URLs to visit, or steps to check. "
                "A human will review before marking this task complete."
            )
            session_prompt = "\n\n".join(prompt_parts)

            # Start the session
            success = await session_manager.start_session(
                session.id,
                session_prompt
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
                await self._mark_task_ready_for_review(task.id, session.exit_code)
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

    async def _mark_task_ready_for_review(self, task_id: int, exit_code: int):
        """Mark a task as ready for review (not completed — user must approve)."""
        try:
            task = await db.get_task(task_id)
            if not task:
                return

            await db.update_task(
                task_id,
                TaskUpdate(
                    status=TaskStatus.READY_FOR_REVIEW,
                )
            )

            await event_bus.emit(
                "task.ready_for_review",
                {"task_id": task_id, "exit_code": exit_code},
                entity_type="task",
                entity_id=task.uuid,
            )

            # Build a useful review comment from session output
            review_comment = await self._build_review_comment(task, exit_code)

            # Auto-PR if project has git_repo and task has a branch
            pr_url = None
            metadata = task.metadata or {}
            branch_name = metadata.get("branch")
            worktree_path_str = metadata.get("worktree_path")
            repo_dir_str = metadata.get("repo_dir")

            if task.project_id and branch_name:
                project = await db.get_project(task.project_id)
                if project and project.git_repo:
                    # Use worktree path for commit if available, else fall back
                    commit_dir = Path(worktree_path_str) if worktree_path_str else Path(project.working_directory)
                    repo_dir = Path(repo_dir_str) if repo_dir_str else Path(project.working_directory)
                    try:
                        await git_manager.commit_and_push(
                            commit_dir, branch_name,
                            f"Task #{task_id}: {task.title}"
                        )
                        pr_url = await git_manager.create_pr(
                            project.git_repo,
                            branch_name,
                            task.title,
                            review_comment[:65000],
                            commit_dir,
                        )
                        await db.update_task(
                            task_id,
                            TaskUpdate(metadata={"pr_url": pr_url})
                        )
                        review_comment += f"\n\n**Pull Request:** {pr_url}"
                        logger.info(f"Created PR for task {task_id}: {pr_url}")
                    except Exception as e:
                        logger.warning(f"Failed to create PR for task {task_id}: {e}")
                        review_comment += f"\n\n*Auto-PR failed: {e}*"

                    # Clean up worktree — code is on the remote branch now
                    if worktree_path_str:
                        try:
                            await git_manager.remove_worktree(repo_dir, Path(worktree_path_str))
                            logger.info(f"Cleaned up worktree for task {task_id}")
                        except Exception as e:
                            logger.warning(f"Failed to remove worktree for task {task_id}: {e}")

            from ..storage.models import CommentCreate
            await db.create_comment(CommentCreate(
                task_id=task_id,
                content=review_comment,
                author="system",
            ))

            logger.info(f"Task {task_id} ready for review (exit code {exit_code})")

            # Check if parent should auto-complete
            if task.parent_task_id:
                await self._check_parent_completion(task.parent_task_id)

        except Exception as e:
            logger.error(f"Failed to mark task {task_id} as ready for review: {e}")

    def _extract_text_from_jsonl(self, raw: str) -> str:
        """Extract readable assistant text from a JSONL session log.

        The stdout log contains one JSON object per line. Assistant text lives in
        ``message.content[].text`` for type=assistant lines, and in ``.result``
        for the final type=result line.
        """
        import json

        chunks: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                # Not JSON — include as-is (shouldn't happen, but safe)
                chunks.append(line)
                continue

            msg_type = obj.get("type")
            if msg_type == "result":
                result_text = obj.get("result", "")
                if result_text:
                    chunks.append(result_text)
            elif msg_type == "assistant":
                content_list = (obj.get("message") or {}).get("content", [])
                for block in content_list:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            chunks.append(text)

        return "\n\n".join(chunks)

    async def _build_review_comment(self, task: Task, exit_code: int) -> str:
        """Extract testing instructions from session output, or summarize it."""
        try:
            if not task.active_session_id:
                return f"Session finished (exit code {exit_code}). No session output available."

            session = await db.get_session(task.active_session_id)
            if not session or not session.stdout_path:
                return f"Session finished (exit code {exit_code}). No session output available."

            stdout_path = Path(session.stdout_path)
            if not stdout_path.exists():
                return f"Session finished (exit code {exit_code}). Session log not found."

            # Parse JSONL to get readable assistant text
            raw = stdout_path.read_text(errors="replace")
            text = self._extract_text_from_jsonl(raw)

            if not text.strip():
                return f"Session finished (exit code {exit_code}). No readable output found."

            # Look for "How to test" section (case-insensitive)
            import re
            match = re.search(
                r'(?:^|\n)#{1,3}\s*[Hh]ow\s+to\s+[Tt]est.*?\n(.*)',
                text, re.DOTALL
            )
            if match:
                instructions = match.group(0).strip()
                if len(instructions) > 1500:
                    instructions = instructions[:1500] + "..."
                return instructions

            # No "How to test" section — take the tail of the extracted text
            lines = text.strip().splitlines()
            tail = "\n".join(lines[-40:]) if len(lines) > 40 else "\n".join(lines)
            if len(tail) > 1500:
                tail = tail[-1500:]
            return (
                f"Session finished (exit code {exit_code}). "
                f"No 'How to test' section found. Last output:\n\n{tail}"
            )

        except Exception as e:
            logger.error(f"Failed to build review comment for task {task.id}: {e}")
            return f"Session finished (exit code {exit_code})."

    async def _mark_task_failed(self, task_id: int, error: str):
        """Mark a task as failed and requeue it for retry."""
        try:
            task = await db.get_task(task_id)
            if not task:
                return

            # Clean up worktree if one exists
            metadata = task.metadata or {}
            worktree_path_str = metadata.get("worktree_path")
            repo_dir_str = metadata.get("repo_dir")
            branch_name = metadata.get("branch")
            if worktree_path_str and repo_dir_str:
                try:
                    await git_manager.remove_worktree(Path(repo_dir_str), Path(worktree_path_str))
                    # Also delete the branch since this failed (no PR)
                    if branch_name:
                        await git_manager.delete_branch(Path(repo_dir_str), branch_name, remote=False)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up worktree for task {task_id}: {cleanup_err}")

            # Requeue: reset to pending so it can be retried
            retry_count = metadata.get("retry_count", 0) + 1
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
                        "worktree_path": None,
                        "repo_dir": None,
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

            terminal = {
                TaskStatus.COMPLETED, TaskStatus.FAILED,
                TaskStatus.CANCELLED, TaskStatus.READY_FOR_REVIEW,
            }
            if not all(s.status in terminal for s in subtasks):
                return

            # All subtasks done — determine parent status
            any_failed = any(s.status == TaskStatus.FAILED for s in subtasks)
            any_reviewing = any(s.status == TaskStatus.READY_FOR_REVIEW for s in subtasks)

            if any_failed:
                new_status = TaskStatus.FAILED
            elif any_reviewing:
                new_status = TaskStatus.READY_FOR_REVIEW
            else:
                new_status = TaskStatus.COMPLETED

            update_fields = {"status": new_status}
            if new_status == TaskStatus.COMPLETED:
                update_fields["completed_at"] = datetime.utcnow()

            await db.update_task(parent_id, TaskUpdate(**update_fields))

            await event_bus.emit(
                f"task.{new_status}",
                {"task_id": parent_id, "auto_completed": True},
                entity_type="task",
                entity_id=parent.uuid,
            )

            logger.info(f"Parent task {parent_id} auto-set to status: {new_status}")

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

            # Clean up worktree and branch
            metadata = task.metadata or {}
            worktree_path_str = metadata.get("worktree_path")
            repo_dir_str = metadata.get("repo_dir")
            branch_name = metadata.get("branch")
            if worktree_path_str and repo_dir_str:
                try:
                    await git_manager.remove_worktree(Path(repo_dir_str), Path(worktree_path_str))
                    if branch_name:
                        await git_manager.delete_branch(Path(repo_dir_str), branch_name, remote=False)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up worktree for cancelled task {task_id}: {cleanup_err}")

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


    async def cleanup_stale_worktrees(self):
        """Garbage-collect worktrees for tasks in terminal states.

        Called periodically by the heartbeat. Handles edge cases like
        server crashes where normal cleanup didn't run.
        """
        try:
            # Get all projects that use git
            projects = await db.list_projects()
            git_projects = [p for p in projects if p.git_repo]

            if not git_projects:
                return

            # Collect branches for currently active (non-terminal) tasks
            active_statuses = {
                TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.ASSESSING
            }
            all_tasks = await db.list_tasks()
            active_branches = set()
            for task in all_tasks:
                if task.status in active_statuses:
                    branch = (task.metadata or {}).get("branch")
                    if branch:
                        active_branches.add(branch)

            # Run GC on each project's repo
            for project in git_projects:
                repo_dir = Path(project.working_directory)
                if repo_dir.exists():
                    await git_manager.cleanup_stale_worktrees(repo_dir, active_branches)

        except Exception as e:
            logger.error(f"Failed to cleanup stale worktrees: {e}")


# Global task scheduler instance
task_scheduler = TaskScheduler()
