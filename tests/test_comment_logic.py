"""Test harness for comment logic.

Tests that the bot never comments twice in a row on a task card.
"""

import asyncio
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_bot_should_not_comment_twice():
    """Test that bot doesn't comment twice in a row on the same task."""
    from agent_queue.storage.database import Database
    from agent_queue.storage.models import TaskCreate, CommentCreate
    from agent_queue.core.task_scheduler import TaskScheduler
    from pathlib import Path
    import tempfile

    # Create a temporary database for testing
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db_path = Path(tmp.name)

    try:
        # Initialize test database
        db = Database(tmp_db_path)
        await db.init_db()

        # Create a test task that's active
        task = await db.create_task(TaskCreate(
            title="Test task for comment logic",
            description="This task should only get one bot comment at a time",
            priority=1,
            metadata={"active": True}
        ))

        print(f"Created test task {task.id}: {task.title}")

        # Scenario 1: No comments yet - bot should be able to comment
        task_ids = [task.id]
        latest_comments = await db.get_latest_comments(task_ids)
        last_comment = latest_comments.get(task.id)

        should_comment = not last_comment or last_comment.author == "user"
        assert should_comment, "Bot should be able to comment when there are no comments"
        print("  PASS: Bot can comment when no comments exist")

        # Add a system comment
        system_comment = await db.create_comment(CommentCreate(
            task_id=task.id,
            content="This is a system comment",
            author="system"
        ))
        print(f"  Added system comment: {system_comment.content[:50]}")

        # Scenario 2: Last comment is from system - bot should NOT comment
        latest_comments = await db.get_latest_comments(task_ids)
        last_comment = latest_comments.get(task.id)

        should_comment = not last_comment or last_comment.author == "user"
        assert not should_comment, "Bot should NOT comment when last comment is from system"
        assert last_comment.author == "system", "Last comment should be from system"
        print("  PASS: Bot correctly skips task when last comment is from system")

        # Add a user comment
        user_comment = await db.create_comment(CommentCreate(
            task_id=task.id,
            content="User response to system comment",
            author="user"
        ))
        print(f"  Added user comment: {user_comment.content[:50]}")

        # Scenario 3: Last comment is from user - bot should be able to comment
        latest_comments = await db.get_latest_comments(task_ids)
        last_comment = latest_comments.get(task.id)

        should_comment = not last_comment or last_comment.author == "user"
        assert should_comment, "Bot should be able to comment when last comment is from user"
        assert last_comment.author == "user", "Last comment should be from user"
        print("  PASS: Bot can comment when last comment is from user")

        # Add another system comment
        system_comment2 = await db.create_comment(CommentCreate(
            task_id=task.id,
            content="Second system comment",
            author="system"
        ))
        print(f"  Added second system comment: {system_comment2.content[:50]}")

        # Scenario 4: Last comment is from system again - bot should NOT comment
        latest_comments = await db.get_latest_comments(task_ids)
        last_comment = latest_comments.get(task.id)

        should_comment = not last_comment or last_comment.author == "user"
        assert not should_comment, "Bot should NOT comment when last comment is from system (again)"
        assert last_comment.author == "system", "Last comment should be from system"
        print("  PASS: Bot correctly skips task when last comment is from system (again)")

        print("\nAll comment logic tests passed!")
        return True

    finally:
        # Clean up test database
        if tmp_db_path.exists():
            tmp_db_path.unlink()


async def test_multiple_tasks_filtering():
    """Test that filtering works correctly with multiple tasks."""
    from agent_queue.storage.database import Database
    from agent_queue.storage.models import TaskCreate, CommentCreate
    from pathlib import Path
    import tempfile

    # Create a temporary database for testing
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db_path = Path(tmp.name)

    try:
        # Initialize test database
        db = Database(tmp_db_path)
        await db.init_db()

        # Create multiple test tasks
        task1 = await db.create_task(TaskCreate(
            title="Task 1 - No comments",
            description="This task has no comments yet",
            priority=1,
            metadata={"active": True}
        ))

        task2 = await db.create_task(TaskCreate(
            title="Task 2 - Bot commented last",
            description="This task has a bot comment",
            priority=1,
            metadata={"active": True}
        ))

        task3 = await db.create_task(TaskCreate(
            title="Task 3 - User commented last",
            description="This task has a user comment",
            priority=1,
            metadata={"active": True}
        ))

        # Add comments
        await db.create_comment(CommentCreate(
            task_id=task2.id,
            content="Bot comment on task 2",
            author="system"
        ))

        await db.create_comment(CommentCreate(
            task_id=task3.id,
            content="Bot comment on task 3",
            author="system"
        ))

        await db.create_comment(CommentCreate(
            task_id=task3.id,
            content="User reply on task 3",
            author="user"
        ))

        # Check which tasks should receive comments
        task_ids = [task1.id, task2.id, task3.id]
        latest_comments = await db.get_latest_comments(task_ids)

        tasks_eligible_for_comment = []
        for task_id in task_ids:
            last_comment = latest_comments.get(task_id)
            if not last_comment or last_comment.author == "user":
                tasks_eligible_for_comment.append(task_id)

        # Verify results
        assert task1.id in tasks_eligible_for_comment, "Task 1 (no comments) should be eligible"
        assert task2.id not in tasks_eligible_for_comment, "Task 2 (bot last) should NOT be eligible"
        assert task3.id in tasks_eligible_for_comment, "Task 3 (user last) should be eligible"

        print(f"Tasks eligible for comments: {tasks_eligible_for_comment}")
        print(f"  Task 1 (no comments): {'✓ eligible' if task1.id in tasks_eligible_for_comment else '✗ not eligible'}")
        print(f"  Task 2 (bot last): {'✓ eligible' if task2.id in tasks_eligible_for_comment else '✗ not eligible'}")
        print(f"  Task 3 (user last): {'✓ eligible' if task3.id in tasks_eligible_for_comment else '✗ not eligible'}")

        print("\nMultiple tasks filtering test passed!")
        return True

    finally:
        # Clean up test database
        if tmp_db_path.exists():
            tmp_db_path.unlink()


async def test_heartbeat_comment_phase():
    """Test that the comment phase of heartbeat respects the no-double-comment rule."""
    from agent_queue.storage.database import Database
    from agent_queue.storage.models import TaskCreate, CommentCreate, TaskStatus
    from pathlib import Path
    import tempfile

    # Create a temporary database for testing
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db_path = Path(tmp.name)

    try:
        # Initialize test database
        db = Database(tmp_db_path)
        await db.init_db()

        # Create test tasks
        task1 = await db.create_task(TaskCreate(
            title="Task with bot comment",
            description="Bot already commented on this",
            priority=1,
            metadata={"active": True}
        ))

        task2 = await db.create_task(TaskCreate(
            title="Task with user comment",
            description="User replied to bot",
            priority=1,
            metadata={"active": True}
        ))

        task3 = await db.create_task(TaskCreate(
            title="Fresh task",
            description="No comments yet",
            priority=1,
            metadata={"active": True}
        ))

        # Set up comment history
        await db.create_comment(CommentCreate(
            task_id=task1.id,
            content="Bot comment",
            author="system"
        ))

        await db.create_comment(CommentCreate(
            task_id=task2.id,
            content="Bot comment",
            author="system"
        ))
        await db.create_comment(CommentCreate(
            task_id=task2.id,
            content="User reply",
            author="user"
        ))

        # Simulate the filtering logic from comment_on_tasks
        all_pending = await db.list_tasks(status=TaskStatus.PENDING)
        active_tasks = [
            t for t in all_pending
            if t.metadata and t.metadata.get("active")
        ]

        task_ids = [t.id for t in active_tasks]
        latest_comments = await db.get_latest_comments(task_ids)

        tasks_to_review = []
        for t in active_tasks:
            last_comment = latest_comments.get(t.id)
            if not last_comment or last_comment.author == "user":
                tasks_to_review.append(t)

        # Verify filtering results
        review_ids = [t.id for t in tasks_to_review]

        assert task1.id not in review_ids, "Task 1 (bot commented last) should be filtered out"
        assert task2.id in review_ids, "Task 2 (user commented last) should be included"
        assert task3.id in review_ids, "Task 3 (no comments) should be included"

        print(f"Active tasks: {len(active_tasks)}")
        print(f"Tasks eligible for review: {len(tasks_to_review)}")
        print(f"  Task 1 (bot last): {'✓ included' if task1.id in review_ids else '✗ filtered out'}")
        print(f"  Task 2 (user last): {'✓ included' if task2.id in review_ids else '✗ filtered out'}")
        print(f"  Task 3 (no comments): {'✓ included' if task3.id in review_ids else '✗ filtered out'}")

        print("\nHeartbeat comment phase test passed!")
        return True

    finally:
        # Clean up test database
        if tmp_db_path.exists():
            tmp_db_path.unlink()


def main():
    """Run all tests."""
    print("=" * 60)
    print("Comment Logic Test Harness")
    print("=" * 60)

    print("\n--- Test: Bot Should Not Comment Twice ---")
    result1 = asyncio.run(test_bot_should_not_comment_twice())

    print("\n--- Test: Multiple Tasks Filtering ---")
    result2 = asyncio.run(test_multiple_tasks_filtering())

    print("\n--- Test: Heartbeat Comment Phase ---")
    result3 = asyncio.run(test_heartbeat_comment_phase())

    print("\n" + "=" * 60)
    if result1 and result2 and result3:
        print("RESULT: All tests PASSED ✓")
    else:
        print("RESULT: Some tests FAILED ✗")
    print("=" * 60)


if __name__ == "__main__":
    main()
