# Git workflow

## Worktree lifecycle

Each task that targets a project with a git repo gets an isolated worktree:

1. **Create**: `git worktree add -b <branch> <path> origin/<default>` in the project's main repo
2. **Execute**: Claude Code runs in the worktree directory, not the main repo
3. **Commit + push**: Changes are committed and pushed from the worktree
4. **PR**: Created via `gh pr create` from the worktree
5. **Cleanup**: Worktree is removed after commit+push. The branch persists on remote for the PR.

Worktrees live in `~/agent-queue-worktrees/<branch-name>/`.

## Branch naming

Format: `task-{task_id}-{slug}`

The slug is derived from the task title: lowercased, non-alphanumeric characters stripped, spaces replaced with hyphens, truncated to 40 characters.

## Cleanup

### Normal cleanup
- **Completed tasks**: Worktree removed after successful commit+push+PR creation
- **Failed tasks**: Worktree removed, local branch deleted (no remote push happened)
- **Cancelled tasks**: Same as failed

### Garbage collection
Every 10th heartbeat, the scheduler runs `cleanup_stale_worktrees()`:
1. Lists all worktrees via `git worktree list --porcelain`
2. Cross-references with tasks in active statuses (pending, executing, assessing)
3. Removes any worktree whose branch is not in the active set
4. Runs `git worktree prune`

This handles edge cases: server crash, killed process, missed cleanup.

## Concurrent execution

Multiple tasks can execute simultaneously, each in its own worktree. The main repo directory is never modified during task execution (worktrees are separate filesystem trees that share the `.git` directory).

`MAX_CONCURRENT_TASKS` (default: 2) controls how many tasks run in parallel.
