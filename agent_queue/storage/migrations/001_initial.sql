-- Initial database schema for agent queue

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, assessing, executing, completed, failed, cancelled
    priority INTEGER DEFAULT 0,
    position INTEGER NOT NULL,  -- Queue position for drag-drop reordering

    -- Hierarchy (subtask support)
    parent_task_id INTEGER,  -- NULL for top-level tasks, FK for subtasks

    -- Assessment results
    complexity TEXT,  -- simple, medium, complex
    recommended_model TEXT,

    -- Execution tracking
    active_session_id INTEGER,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- Metadata (JSON)
    metadata TEXT DEFAULT '{}',

    FOREIGN KEY (active_session_id) REFERENCES sessions(id),
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_position ON tasks(position);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    task_id INTEGER NOT NULL,

    -- Claude Code CLI session info
    claude_session_id TEXT,  -- If we can extract it
    working_directory TEXT NOT NULL,
    model TEXT NOT NULL,

    -- Status tracking
    status TEXT NOT NULL DEFAULT 'created',  -- created, running, paused, completed, failed, cancelled
    turn_count INTEGER DEFAULT 0,

    -- Output paths
    stdout_path TEXT,
    stderr_path TEXT,

    -- Process info
    pid INTEGER,
    exit_code INTEGER,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    last_heartbeat TIMESTAMP,

    -- Session artifacts (JSON) - for session-to-session handoff
    artifacts TEXT DEFAULT '{}',

    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_task_id ON sessions(task_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

-- Comments table
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    task_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    author TEXT DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_comments_task_id ON comments(task_id);

-- Events table (event sourcing)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,  -- e.g., "task.created", "session.started", "heartbeat.tick"
    entity_type TEXT NOT NULL,  -- e.g., "task", "session", "system"
    entity_id TEXT,  -- UUID of the entity
    payload TEXT NOT NULL,  -- JSON payload
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);

-- Rate limits table (single row cache)
CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Only one row
    tier TEXT,
    messages_used INTEGER DEFAULT 0,
    messages_limit INTEGER DEFAULT 0,
    percent_used REAL DEFAULT 0.0,
    reset_at TIMESTAMP,
    raw_output TEXT,  -- Raw /usage output for debugging
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
