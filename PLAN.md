# Long-Running Claude Code Agent Harness Implementation Plan

## Context

Building a task queue management system that autonomously executes coding tasks using Claude Code CLI. The system runs continuously with a heartbeat that checks rate limits, pulls tasks from a queue, assesses their complexity, and spawns Claude Code sessions to complete them. The queue is managed through a web interface with drag-and-drop reordering (Spotify-style).

**Problem**: Complex projects require multiple coding sessions, but managing long-running agent workflows manually is tedious and error-prone.

**Solution**: An autonomous harness that schedules tasks, monitors rate limits, assesses complexity, and manages Claude Code sessions with full observability.

**Outcome**: Developers can queue multiple tasks and let the system work through them autonomously, with real-time visibility into progress.

## Architecture Overview

### Core Components

```
Web UI (HTML/JS + SSE)
    ↓
FastAPI REST + SSE Server
    ↓
Heartbeat Loop (60s) → Rate Limit Monitor → Task Scheduler
    ↓
Session Manager → Claude Code CLI (subprocess)
    ↓
SQLite Database (tasks, sessions, events)
```

### Key Design Patterns (Inspired by Pi Agent)

1. **Turn-Based Execution**: Each task progresses through discrete turns (LLM call + tool execution)
2. **Event-Driven Architecture**: All state changes emit events for real-time UI updates via SSE
3. **Dual Queue System**: Separate steering (interrupt) and follow-up (deferred) message queues
4. **JSONL Event Sourcing**: Store all events for audit trail and replay capability
5. **Subprocess Integration**: Spawn Claude Code CLI processes rather than reimplementing internals

### Data Flow

```
Heartbeat (60s) → Check rate limits → Pull next pending task
    ↓
Task State: not started | in progress | done
    ↓
not started → Assessment Phase → Execution Phase
in progress → Resume Session → Continue
done → Mark complete → Dequeue
    ↓
Emit events → SSE stream → Web UI updates
```

## Implementation Structure

```
agent-harness/
├── pyproject.toml
├── agent_harness/
│   ├── server.py                     # FastAPI app entry point
│   ├── config.py                     # Configuration
│   │
│   ├── api/                          # REST + SSE endpoints
│   │   ├── tasks.py                  # Task CRUD
│   │   ├── sessions.py               # Session management
│   │   ├── status.py                 # System status
│   │   └── events.py                 # SSE event stream
│   │
│   ├── core/                         # Business logic
│   │   ├── heartbeat.py              # 60s loop coordinator
│   │   ├── task_scheduler.py         # Task state machine
│   │   ├── session_manager.py        # Session lifecycle
│   │   ├── assessment_engine.py      # Complexity analysis
│   │   ├── rate_limit_monitor.py     # Parse ~/.claude stats
│   │   └── event_bus.py              # Event pub/sub
│   │
│   ├── integration/                  # External integrations
│   │   ├── claude_code_cli.py        # Subprocess spawner
│   │   └── output_parser.py          # Parse CLI output
│   │
│   └── storage/                      # Data layer
│       ├── database.py               # SQLite operations
│       ├── models.py                 # Pydantic models
│       └── migrations/
│           └── 001_initial.sql       # Schema
│
├── web/                              # Web UI
│   ├── index.html
│   ├── style.css
│   └── app.js
│
└── data/                             # Runtime data (git-ignored)
    ├── harness.db                    # SQLite database
    └── sessions/                     # Session logs
```

## Database Schema (SQLite)

### Core Tables

**tasks**
- `id`, `uuid`, `title`, `description`, `status`, `priority`, `position`
- `complexity`, `estimated_turns`, `recommended_model`, `decomposed`
- `active_session_id`, `created_at`, `started_at`, `completed_at`
- `metadata` (JSON)

**sessions**
- `id`, `uuid`, `task_id`, `claude_session_id`, `working_directory`, `model`
- `status`, `turn_count`, `stdout_path`, `stderr_path`, `pid`, `exit_code`
- `created_at`, `started_at`, `completed_at`, `last_heartbeat`
- `artifacts` (JSON - session-to-session handoff data)

**comments**
- `id`, `uuid`, `task_id`, `content`, `author`, `created_at`, `updated_at`

**events** (event sourcing)
- `id`, `uuid`, `event_type`, `entity_type`, `entity_id`, `payload`, `created_at`

**rate_limits** (single row cache)
- `tier`, `messages_today`, `last_reset`, `daily_stats` (JSON), `updated_at`

**steering_messages** (dual queue from pi-mono)
- `id`, `session_id`, `message`, `priority`, `processed`, `created_at`

### Task States

- `pending` → Initial state
- `assessing` → Analyzing complexity/requirements
- `executing` → Active Claude Code session running
- `completed` → Successfully finished
- `failed` → Error occurred
- `cancelled` → User cancelled

## Key Implementation Components

### 1. Heartbeat Manager (`core/heartbeat.py`)

Runs every 60 seconds:
1. Parse `~/.claude/stats-cache.json` for rate limit status
2. If not rate-limited, call `TaskScheduler.schedule_next_task()`
3. Emit `heartbeat.tick` event with rate limit info
4. If rate-limited, emit `heartbeat.rate_limited` event

### 2. Rate Limit Monitor (`core/rate_limit_monitor.py`)

```python
class RateLimitMonitor:
    def get_rate_limit_status(self) -> Dict:
        # Parse ~/.claude/stats-cache.json
        # Parse ~/.claude/.credentials.json for tier
        # Calculate messages_today, available, is_limited
        # Return status dict
```

**Challenge**: stats-cache.json may not be real-time
**Solution**: Cache conservatively, add manual override option

### 3. Task Scheduler (`core/task_scheduler.py`)

```python
async def schedule_next_task(self):
    task = get_next_pending_task()  # By position, then priority
    state = determine_task_state(task)

    if state == "not_started":
        await assess_task(task)
        await execute_task(task)
    elif state == "in_progress":
        await resume_task(task)
    elif state == "done":
        await complete_task(task)
```

### 4. Assessment Engine (`core/assessment_engine.py`)

Uses Anthropic SDK to analyze tasks:
- **Complexity**: simple (<5 turns) | medium (5-15) | complex (>15)
- **Model selection**: haiku (simple) | sonnet (most) | opus (complex)
- **Decomposition**: Detect if task needs subtasks
- **Extensible**: Future phases can add dependency analysis, risk assessment, test plans

Prompt structure:
```
Analyze this coding task: {title} / {description}
Return JSON: { complexity, estimated_turns, recommended_model, subtasks[], reasoning }
```

### 5. Session Manager (`core/session_manager.py`)

Manages Claude Code CLI subprocess lifecycle:

```python
async def create_session(task) -> Session:
    # Create DB record, prepare working directory

async def start(session):
    # Spawn: asyncio.create_subprocess_exec(['claude', task.description])
    # Capture stdout/stderr to data/sessions/{uuid}/
    # Stream output to event bus
    # Update turn count on boundaries
    # Emit events: session.started, session.output, session.completed

async def pause/resume/cancel(session):
    # Process control (terminate/kill)
```

**Integration approach**:
- Spawn subprocess: `claude {task.description}` in working directory
- Capture stdout/stderr via pipes
- Write to log files: `data/sessions/{uuid}/stdout.log`
- Detect turn boundaries heuristically (e.g., "Tool execution complete")
- Use exit codes to determine success/failure

**Challenge**: Claude Code CLI isn't designed for programmatic use
**Solution**: Parse output, use exit codes, log everything for manual review

### 6. Event Bus (`core/event_bus.py`)

Pub/sub system for real-time updates:
```python
await event_bus.emit("task.created", {"task_id": 1})
queue = event_bus.subscribe("*")  # All events
event = await queue.get()
```

Connected to SSE endpoint:
```python
@app.get("/api/events/stream")
async def stream_events():
    queue = event_bus.subscribe("*")
    async def generator():
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(generator(), media_type="text/event-stream")
```

### 7. Web UI (`web/app.js`)

Key features:
- **SSE Connection**: `new EventSource('/api/events/stream')`
- **Drag-and-Drop**: HTML5 Drag API for queue reordering
- **Real-Time Updates**: Event handlers update DOM on events
- **Session Streaming**: Display live output from active sessions
- **Task Management**: Add, edit, comment, cancel tasks

## API Endpoints

### Tasks
- `GET /api/tasks` - List tasks (filter by status)
- `POST /api/tasks` - Create task (body: title, description, priority)
- `GET /api/tasks/{id}` - Get task details with sessions/comments
- `PATCH /api/tasks/{id}` - Update task
- `DELETE /api/tasks/{id}` - Cancel task
- `POST /api/tasks/{id}/comments` - Add comment
- `POST /api/tasks/reorder` - Reorder queue (drag-drop)

### Sessions
- `GET /api/sessions/{id}` - Session details
- `GET /api/sessions/{id}/output` - Stream output logs
- `POST /api/sessions/{id}/pause|resume|cancel` - Control session
- `POST /api/sessions/{id}/steer` - Send steering message

### Status
- `GET /api/status` - System status (rate limits, active tasks)
- `GET /api/events/stream` - SSE event stream
- `GET /api/heartbeat` - Health check

## Implementation Phases

### Phase 1: Foundation
1. Create project structure with uv: `uv init agent-harness`
2. Add dependencies: `fastapi`, `uvicorn`, `anthropic`, `sse-starlette`
3. Implement database schema and migrations (SQLite)
4. Create Pydantic models for Task, Session, Comment, Event

### Phase 2: Core Logic
1. Implement `RateLimitMonitor` (parse stats files)
2. Build `HeartbeatManager` (60s async loop)
3. Create `TaskScheduler` (state machine)
4. Implement `AssessmentEngine` (Anthropic SDK call)
5. Build `EventBus` (pub/sub for SSE)

### Phase 3: Integration
1. Implement `ClaudeCodeCLI` subprocess spawner
2. Build `SessionManager` (lifecycle management)
3. Add output capture and streaming
4. Implement turn boundary detection

### Phase 4: API Layer
1. Create FastAPI app with routers
2. Implement task CRUD endpoints
3. Add session management endpoints
4. Build SSE event stream endpoint
5. Add status/monitoring endpoints

### Phase 5: Web UI
1. Create HTML layout (Spotify-style queue)
2. Implement SSE client connection
3. Add drag-and-drop with HTML5 API
4. Build real-time task list updates
5. Add session output viewer
6. Implement task creation/editing forms

### Phase 6: Testing & Polish
1. Write unit tests for core components
2. Integration tests for API endpoints
3. Test session spawning and output capture
4. Add error handling and logging
5. Write deployment documentation

## Verification Steps

### 1. Database Setup
```bash
cd agent-harness
uv run python -c "from agent_harness.storage.database import init_db; init_db()"
sqlite3 data/harness.db ".schema"
```

### 2. Rate Limit Monitor
```bash
uv run python -c "
from agent_harness.core.rate_limit_monitor import RateLimitMonitor
monitor = RateLimitMonitor()
print(monitor.get_rate_limit_status())
"
```

### 3. FastAPI Server
```bash
uv run uvicorn agent_harness.server:app --reload
curl http://localhost:8000/api/heartbeat
```

### 4. Task Creation
```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Test task", "description": "Simple test", "priority": 1}'
```

### 5. SSE Event Stream
```bash
curl -N http://localhost:8000/api/events/stream
```

### 6. End-to-End Test
```bash
# 1. Create a simple task via UI: "Create a hello.py file that prints 'Hello, World!'"
# 2. Wait for heartbeat (60s) to pick it up
# 3. Observe assessment phase
# 4. Watch session spawn and execute
# 5. Verify output streams to UI
# 6. Check task marked as completed
```

## Technical Decisions & Rationale

### FastAPI + SQLite + Asyncio
- **Zero external dependencies** (no Redis, no PostgreSQL setup)
- **Async-native** for background tasks and SSE streaming
- **Production-ready** for single-server deployment
- **Easy migration** to PostgreSQL/Redis if needed later

### Subprocess Integration
- Claude Code CLI not designed for programmatic use
- **Clean process isolation** prevents internal coupling
- **Future-proof** against CLI updates
- Captures full stdout/stderr for debugging

### Event Sourcing
- **Complete audit trail** of all state changes
- **Real-time UI updates** via SSE
- **Replay capability** for debugging
- **Analytics-ready** for future insights

### Turn-Based Execution
- **Simple mental model** (like pi-mono)
- **Observable progress** (every turn is visible)
- **Easy debugging** (trace execution step-by-step)
- **Natural fit** for Claude Code's interactive nature

## Known Limitations & Future Work

### Current Scope (MVP)
- Sequential task execution only (one task at a time)
- Manual task decomposition (no auto-subtasks yet)
- Basic assessment (complexity + model selection only)
- Local single-server deployment
- Heuristic turn detection (may not be perfect)

### Future Enhancements
1. **Enhanced Assessment**: Dependency detection, risk analysis, test strategy
2. **Auto-Decomposition**: Break complex tasks into subtasks automatically
3. **Concurrent Execution**: Run multiple tasks in parallel (with rate limit management)
4. **Session Replay**: Re-run sessions with different parameters
5. **Template Library**: Pre-configured task templates
6. **Notification System**: Email/Slack alerts on completion
7. **Multi-User Support**: Collaborative task queues
8. **Distributed Workers**: Scale to multiple execution nodes
9. **Cost Tracking**: Monitor API costs per task
10. **Learning System**: Improve assessments based on outcomes

## Architecture Inspiration

This design draws from:
- **Pi Agent** (badlogic/pi-mono): Turn-based execution, dual queue, event-driven architecture
- **Anthropic's Long-Running Agent Guidance**: Initializer + coding agent pattern, session artifacts
- **Existing FastAPI Patterns**: `/home/abraham/debate-bot-evolution/debate/prompt_editor/server.py`

Sources:
- [badlogic/pi-mono on GitHub](https://github.com/badlogic/pi-mono)
- [What I learned building pi coding agent](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [2026 Is Agent Harnesses](https://aakashgupta.medium.com/2025-was-agents-2026-is-agent-harnesses-heres-why-that-changes-everything-073e9877655e)
