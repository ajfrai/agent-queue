# Architecture

## Overview

agent-queue is a thin harness that spawns isolated Claude Code sessions to execute coding tasks. The intelligence lives in the repo (CLAUDE.md, linters, tests), not in the harness. The harness manages lifecycle: queue tasks, assess complexity, spawn agents in isolated worktrees, capture results.

## Core components

### Heartbeat (`core/heartbeat.py`)
Central orchestration loop running every 60 seconds. 2-phase cycle:
- **Odd beats: assess** - Batch-assess unassessed tasks via Anthropic API. Assessment includes optional comments (no separate comment phase).
- **Even beats: execute** - Launch up to `MAX_CONCURRENT_TASKS` assessed tasks in parallel. Each gets an isolated git worktree.

Every 10th beat runs garbage collection on stale worktrees.

### Task scheduler (`core/task_scheduler.py`)
State machine managing task lifecycle:
- `dedupe_tasks()` - Remove duplicate pending tasks
- `assess_pending_tasks()` - Batch-assess up to 10 tasks, create comments from assessment
- `execute_next_tasks()` - Fill available execution slots with assessed tasks
- `cleanup_stale_worktrees()` - GC for crashed/interrupted tasks

### Assessment engine (`core/assessment_engine.py`)
LLM-based task analysis using Anthropic API. Determines complexity (simple/medium/complex), recommended model (haiku/sonnet/opus), and whether to decompose. Returns optional comments when the model has useful observations.

### Session manager (`core/session_manager.py`)
Spawns Claude Code CLI processes. Manages stdout/stderr capture, process monitoring, and session lifecycle.

### Git manager (`core/git_manager.py`)
Git operations via subprocess:
- **Worktrees**: `create_worktree()` / `remove_worktree()` for task isolation
- **Branches**: `create_branch()` (legacy), `delete_branch()` for cleanup
- **PR flow**: `commit_and_push()`, `create_pr()` via `gh` CLI
- **GC**: `cleanup_stale_worktrees()` cross-references worktrees against active tasks

### Event bus (`core/event_bus.py`)
Async event emission for UI updates via SSE. All state changes emit events.

## Storage

### Database (`storage/database.py`)
SQLite via aiosqlite. Tables: tasks, sessions, comments, events, rate_limits, projects.

Metadata on tasks is **merged** (not replaced) on update. This is critical for incremental state like assessment results, branch info, worktree paths.

### Models (`storage/models.py`)
Pydantic models for all entities. TaskStatus enum defines the state machine.

## Data flow

```
User creates task (API) -> PENDING
                              |
Heartbeat assess phase -> assess_batch() -> PENDING[assessed]
                              |
Heartbeat execute phase -> create_worktree() -> start_session() -> EXECUTING
                              |
Session completes -> commit_and_push() -> create_pr() -> remove_worktree() -> READY_FOR_REVIEW
                              |
User approves -> COMPLETED
```

## Isolation model

Each executing task gets:
1. Its own git worktree in `~/agent-queue-worktrees/<branch-name>`
2. Its own Claude Code process
3. Its own session log directory

No shared mutable state between concurrent tasks.
