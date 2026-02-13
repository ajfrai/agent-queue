"""Configuration management for the agent queue."""

import argparse
from pathlib import Path
import os


class Config:
    """Application configuration."""

    # Paths
    BASE_DIR = Path(__file__).parent.parent
    DATA_DIR = BASE_DIR / "data"
    DB_PATH = DATA_DIR / "queue.db"
    SESSIONS_DIR = DATA_DIR / "sessions"

    # Server settings
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))

    # Heartbeat settings
    HEARTBEAT_INTERVAL = 300  # seconds (5 minutes)

    # Assessment settings
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ASSESSMENT_MODEL = "claude-sonnet-4-5-20250929"

    # Task settings
    DEFAULT_WORKING_DIR = Path.home()

    # Project settings
    PROJECT_NAME = None
    PROJECT_ID = None
    PROJECT_CONTEXT = ""

    @classmethod
    def ensure_directories(cls):
        """Create necessary directories if they don't exist."""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def apply_args(cls, args):
        """Apply parsed CLI arguments to config."""
        if args.host:
            cls.HOST = args.host
        if args.port:
            cls.PORT = args.port
        if args.project:
            cls.PROJECT_NAME = args.project


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Agent Queue - Autonomous task queue for Claude Code CLI")
    parser.add_argument("-p", "--project", type=str, default=None,
                        help="Project name to scope tasks to")
    parser.add_argument("--list-projects", action="store_true",
                        help="List registered projects and exit")
    parser.add_argument("--host", type=str, default=None,
                        help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None,
                        help="Server port (default: 8000)")
    return parser.parse_args()


config = Config()
