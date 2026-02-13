# agent-queue

Autonomous task queue wrapper around Claude Code CLI. Spawns isolated agents per task, captures results, manages lifecycle.

## Quick orientation

| Path | What it does |
|------|-------------|
| `agent_queue/core/` | Heartbeat loop, task scheduler, assessment engine, session manager, git operations |
| `agent_queue/storage/` | SQLite database, Pydantic models, migrations |
| `agent_queue/api/` | FastAPI REST endpoints (tasks, sessions, projects, events, status) |
| `agent_queue/integration/` | Claude Code CLI process management |
| `web/` | Frontend (HTML/JS/CSS) |
| `data/` | Runtime data (SQLite DB, session logs) |

## Key docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Domain map, data flow, component responsibilities
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md) - Naming, error handling, event patterns
- [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md) - Worktree lifecycle, branch naming, PR flow

## Task lifecycle

```
PENDING -> (assess) -> PENDING[assessed] -> (execute) -> EXECUTING -> READY_FOR_REVIEW -> COMPLETED
                                         \-> DECOMPOSED -> subtasks follow same flow
```

Tasks must be activated (`metadata.active = true`) before the scheduler picks them up.

## Running

```bash
python -m agent_queue.server -p <project-name>
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | API key for assessment model |
| `MAX_CONCURRENT_TASKS` | `2` | Max parallel task executions |
| `WORKTREES_DIR` | `~/agent-queue-worktrees` | Where task worktrees are created |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
