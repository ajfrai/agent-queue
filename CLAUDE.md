# Agent Queue Development Guide

## Repository Structure

This is a nested copy of the agent-queue repository inside itself for testing purposes. This nesting is acceptable and does not need to be moved.

## Creating Pull Requests

When creating PRs in this repository:

1. **Always pull and sync with remote first:**
   ```bash
   git fetch origin
   git checkout main
   git reset --hard origin/main
   ```

2. **Create a clean task branch from main:**
   ```bash
   git checkout -b task-XX-<description> main
   ```

3. **Make your changes, commit, and push:**
   ```bash
   git add <files>
   git commit -m "..."
   git push -u origin task-XX-<description>
   ```

4. **Create PR with gh CLI:**
   ```bash
   gh pr create --title "Task #XX: ..." --body "..."
   ```

**Why this process:** Multiple task branches can diverge from different base commits, which causes "No commits between" and "Head sha can't be blank" errors. Starting fresh from origin/main ensures each PR has a clean history and avoids conflicts.

## Testing Changes

Always test changes before opening a PR. For heartbeat-related changes:

```bash
python -m agent_queue.server
```

Check the log output for confirmation messages (e.g., heartbeat interval confirmation).
