# Agent Queue Development Guide

## Repository Structure

This is a nested copy of the agent-queue repository inside itself for testing purposes. This nesting is acceptable and does not need to be moved.

## Creating Pull Requests

### The Correct Process (Always Follow This)

When creating PRs in this repository, follow these steps **exactly**:

1. **Sync main with remote (critical step):**
   ```bash
   git fetch origin
   git checkout main
   git reset --hard origin/main
   ```
   This ensures your local main matches the latest remote state.

2. **Create a clean task branch from the synced main:**
   ```bash
   git checkout -b task-XX-<description> main
   ```
   **Important:** Always branch from `main`, not from another task branch.

3. **Make your changes, commit, and push:**
   ```bash
   git add <files>
   git commit -m "Task #XX: Clear description of changes"
   git push -u origin task-XX-<description>
   ```

4. **Verify before creating PR:**
   ```bash
   git log main..HEAD --oneline
   ```
   This should show ONLY your new commits. If you see extra commits from the task branch history, something went wrong.

5. **Create PR with gh CLI:**
   ```bash
   gh pr create --title "Task #XX: ..." --body "..."
   ```

### Troubleshooting PR Creation Errors

#### Error: "Head sha can't be blank" or "No commits between"

This happens when:
- A task branch was created from an old base commit before newer commits were added to main
- The task branch has diverged from the current main branch
- Local main is out of sync with origin/main

**Solution:** Use the **Clean Branch Process**:
```bash
# 1. Sync main with remote
git fetch origin
git checkout main
git reset --hard origin/main

# 2. Create a new clean branch from synced main
git checkout -b task-XX-clean main

# 3. Cherry-pick only the commits you want from the old task branch
# First, find the commits: git log task-XX-old --oneline
# Then cherry-pick each unique commit:
git cherry-pick <commit-hash>
git cherry-pick <commit-hash>
# ... repeat for each commit

# 4. Verify only your commits are present
git log main..HEAD --oneline

# 5. Push the clean branch
git push -u origin task-XX-clean -f

# 6. Create the PR from this clean branch
gh pr create --title "Task #XX: ..." --body "..."
```

### Why This Works

Multiple task branches can diverge from different base commits, causing PR creation failures. By:
- Always syncing main first with `git reset --hard origin/main`
- Creating task branches from the current synced main
- Verifying commits with `git log main..HEAD --oneline`

You ensure each PR has a clean linear history from the latest main, preventing all "No commits between", "Head sha can't be blank", and "Base sha can't be blank" errors.

## Testing Changes

Always test changes before opening a PR. For heartbeat-related changes:

```bash
python -m agent_queue.server
```

Check the log output for confirmation messages (e.g., heartbeat interval confirmation).
