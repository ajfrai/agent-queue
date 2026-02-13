"""Git operations manager for project repositories."""

import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

REPOS_DIR = Path.home() / "agent-queue-repos"


def slugify(text: str, max_len: int = 40) -> str:
    """Sanitize text into a git-branch-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:max_len]


async def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    logger.info(f"git_manager: running {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def get_gh_owner() -> str:
    """Get the authenticated GitHub username."""
    rc, out, err = await _run(["gh", "api", "user", "--jq", ".login"])
    if rc != 0:
        raise RuntimeError(f"Failed to get GitHub user: {err}")
    return out.strip()


async def create_repo(name: str, private: bool = False) -> tuple[str, Path]:
    """Create a new GitHub repo, clone it locally, and seed with a README.

    Args:
        name: Repository name (no owner prefix).
        private: Whether the repo should be private.

    Returns:
        (owner/repo, local_path) tuple.
    """
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    local_path = REPOS_DIR / name

    visibility = "--private" if private else "--public"
    rc, out, err = await _run([
        "gh", "repo", "create", name,
        visibility, "--clone",
        "--description", f"Created by agent-queue",
    ], cwd=REPOS_DIR)
    if rc != 0:
        raise RuntimeError(f"Failed to create repo '{name}': {err}")

    # gh repo create --clone puts it in REPOS_DIR/name
    if not local_path.exists():
        # Sometimes gh outputs the path differently
        raise RuntimeError(f"Repo created but clone not found at {local_path}")

    # Seed with a README so the default branch exists on remote
    readme = local_path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {name}\n")
        await _run(["git", "add", "README.md"], cwd=local_path)
        await _run(["git", "commit", "-m", "Initial commit"], cwd=local_path)
        # Push to whatever the current branch is
        rc, branch, _ = await _run(["git", "branch", "--show-current"], cwd=local_path)
        branch = branch or "main"
        await _run(["git", "push", "-u", "origin", branch], cwd=local_path)

    # Resolve owner/repo
    owner = await get_gh_owner()
    owner_repo = f"{owner}/{name}"

    logger.info(f"Created repo {owner_repo} at {local_path}")
    return owner_repo, local_path


async def clone_repo(owner_repo: str) -> Path:
    """Clone a GitHub repo to ~/agent-queue-repos/{repo}/.

    Args:
        owner_repo: GitHub repo in "owner/repo" format.

    Returns:
        Path to the cloned repository.

    Raises:
        RuntimeError: If clone fails.
    """
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    repo_name = owner_repo.split("/")[-1]
    clone_path = REPOS_DIR / repo_name

    if clone_path.exists():
        # Already cloned — pull latest instead
        rc, out, err = await _run(["git", "pull"], cwd=clone_path)
        if rc != 0:
            logger.warning(f"git pull failed in {clone_path}: {err}")
        return clone_path

    rc, out, err = await _run(
        ["gh", "repo", "clone", owner_repo, str(clone_path)]
    )
    if rc != 0:
        raise RuntimeError(f"Failed to clone {owner_repo}: {err}")

    logger.info(f"Cloned {owner_repo} to {clone_path}")
    return clone_path


async def get_default_branch(working_dir: Path) -> str:
    """Read the default branch from the cloned repo."""
    rc, out, err = await _run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        cwd=working_dir,
    )
    if rc == 0 and out:
        # Returns e.g. "origin/main" — strip the "origin/" prefix
        return out.replace("origin/", "")

    # Fallback: try to read from remote
    rc, out, err = await _run(
        ["git", "remote", "show", "origin"],
        cwd=working_dir,
    )
    if rc == 0:
        for line in out.splitlines():
            if "HEAD branch:" in line:
                branch = line.split(":")[-1].strip()
                if branch and branch != "(unknown)":
                    return branch

    # Fallback: current local branch
    rc, out, err = await _run(
        ["git", "branch", "--show-current"],
        cwd=working_dir,
    )
    if rc == 0 and out:
        return out

    return "main"


async def create_branch(working_dir: Path, branch_name: str):
    """Create and checkout a new git branch."""
    # First ensure we're on the default branch and up to date
    default = await get_default_branch(working_dir)
    await _run(["git", "checkout", default], cwd=working_dir)
    await _run(["git", "pull", "--ff-only"], cwd=working_dir)

    rc, out, err = await _run(
        ["git", "checkout", "-b", branch_name],
        cwd=working_dir,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create branch {branch_name}: {err}")

    logger.info(f"Created branch {branch_name} in {working_dir}")


async def commit_and_push(working_dir: Path, branch_name: str, message: str):
    """Stage all changes, commit, and push the branch."""
    await _run(["git", "add", "-A"], cwd=working_dir)

    # Check if there are changes to commit
    rc, out, _ = await _run(["git", "diff", "--cached", "--quiet"], cwd=working_dir)
    if rc == 0:
        logger.info("No changes to commit")
        return

    rc, out, err = await _run(
        ["git", "commit", "-m", message],
        cwd=working_dir,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to commit: {err}")

    rc, out, err = await _run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=working_dir,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to push branch {branch_name}: {err}")

    logger.info(f"Pushed branch {branch_name}")


async def create_pr(
    owner_repo: str, branch: str, title: str, body: str, working_dir: Path
) -> str:
    """Create a pull request using gh CLI.

    Returns the PR URL.
    """
    rc, out, err = await _run(
        [
            "gh", "pr", "create",
            "--repo", owner_repo,
            "--head", branch,
            "--title", title,
            "--body", body,
        ],
        cwd=working_dir,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create PR: {err}")

    # gh pr create outputs the PR URL
    pr_url = out.strip()
    logger.info(f"Created PR: {pr_url}")
    return pr_url
