import asyncio
import sys

from agent_queue.config import parse_args, config


def list_projects():
    """List registered projects and exit."""
    from agent_queue.storage.database import db

    async def _list():
        await db.init_db()
        projects = await db.list_projects()
        if not projects:
            print("No projects registered.")
            print("Register one via POST /api/projects or the web UI.")
            return
        # Header
        print(f"{'ID':<5} {'Name':<20} {'Working Directory':<35} {'Git Repo':<30} {'Summary'}")
        print("-" * 120)
        for p in projects:
            summary = (p.summary[:40] + "...") if len(p.summary) > 40 else p.summary
            repo = p.git_repo or "-"
            print(f"{p.id:<5} {p.name:<20} {p.working_directory:<35} {repo:<30} {summary}")

    asyncio.run(_list())


def main():
    args = parse_args()
    config.apply_args(args)

    if args.list_projects:
        list_projects()
        sys.exit(0)

    import uvicorn
    uvicorn.run(
        "agent_queue.server:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
