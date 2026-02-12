"""Configuration management for the agent queue."""

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
    HEARTBEAT_INTERVAL = 60  # seconds

    # Assessment settings
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ASSESSMENT_MODEL = "claude-sonnet-4-5-20250929"

    # Task settings
    DEFAULT_WORKING_DIR = Path.home()

    @classmethod
    def ensure_directories(cls):
        """Create necessary directories if they don't exist."""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


config = Config()
