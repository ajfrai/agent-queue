# Conventions

## Naming

- **Branch names**: `task-{id}-{slugified-title}` (max 40 char slug)
- **Task statuses**: lowercase snake_case (`pending`, `executing`, `ready_for_review`)
- **Event types**: dotted namespace (`task.assessed`, `heartbeat.tick`)
- **Module globals**: `snake_case` singletons at module bottom (`task_scheduler`, `heartbeat_manager`)

## Error handling

- The heartbeat loop never crashes. All phase actions are wrapped in try/except.
- Errors are logged and emitted as events but never propagate to kill the loop.
- Failed tasks are requeued to PENDING with `retry_count` incremented in metadata.
- Git/worktree cleanup failures are warnings, not errors. The GC pass handles leftovers.

## Event patterns

All state transitions emit events via `event_bus.emit()`:
```python
await event_bus.emit(
    "task.executing",           # event_type
    {"task_id": task_id},       # payload
    entity_type="task",         # for routing
    entity_id=task.uuid,        # for filtering
)
```

## Database patterns

- All entities have both `id` (int, auto-increment) and `uuid` (string, UUID4).
- `id` is used internally. `uuid` is used in API responses and event routing.
- Task metadata is **merged** via `dict.update()`, never replaced. Set keys to `None` to clear them.
- Migrations are auto-applied from `storage/migrations/*.sql` on startup.

## Assessment patterns

- Assessment uses temperature=0.0 for deterministic results.
- Decomposition bias: almost always `should_decompose=false`. Only decompose for clearly independent multi-session work.
- The assessment model is separate from the execution model. Assessment always uses Sonnet; execution uses the model recommended by assessment.
