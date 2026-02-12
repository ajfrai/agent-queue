"""Heartbeat manager for the agent queue."""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from ..config import config
from .rate_limit_monitor import rate_limit_monitor
from .task_scheduler import task_scheduler
from .event_bus import event_bus

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """Manages the heartbeat loop that coordinates task execution."""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.last_beat: Optional[datetime] = None
        self.last_rate_status = None  # Cached for UI reads

    async def start(self):
        """Start the heartbeat loop."""
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"Heartbeat started (interval: {config.HEARTBEAT_INTERVAL}s)")

        await event_bus.emit(
            "heartbeat.started",
            {"interval": config.HEARTBEAT_INTERVAL},
            entity_type="system",
        )

    async def stop(self):
        """Stop the heartbeat loop."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Heartbeat stopped")

        await event_bus.emit(
            "heartbeat.stopped",
            {},
            entity_type="system",
        )

    async def _heartbeat_loop(self):
        """Main heartbeat loop - never crashes."""
        while self._running:
            try:
                await self._beat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)
                # Emit a minimal tick so the UI knows we're alive
                try:
                    await event_bus.emit(
                        "heartbeat.tick",
                        {
                            "timestamp": datetime.utcnow().isoformat(),
                            "rate_limit": None,
                            "error": str(e),
                        },
                        entity_type="system",
                    )
                except Exception:
                    pass

            try:
                await asyncio.sleep(config.HEARTBEAT_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _beat(self):
        """Execute a single heartbeat cycle."""
        self.last_beat = datetime.utcnow()

        # 1. Check rate limits - never let this crash the heartbeat
        rate_status = None
        try:
            rate_status = await rate_limit_monitor.get_rate_limit_status()
            self.last_rate_status = rate_status
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")

        # Build rate limit payload (always well-formed, even if check failed)
        rate_payload = None
        if rate_status:
            rate_payload = {
                "tier": rate_status.tier or "unknown",
                "messages_used": rate_status.messages_used or 0,
                "messages_limit": rate_status.messages_limit or 0,
                "percent_used": rate_status.percent_used or 0.0,
                "is_limited": rate_status.is_limited,
                "reset_at": rate_status.reset_at.isoformat() if rate_status.reset_at else None,
            }

        # Emit heartbeat event
        await event_bus.emit(
            "heartbeat.tick",
            {
                "timestamp": self.last_beat.isoformat(),
                "rate_limit": rate_payload,
            },
            entity_type="system",
        )

        # 2. If rate limited, skip scheduling
        if rate_status and rate_status.is_limited:
            logger.info(
                f"Rate limited. Reset at: "
                f"{rate_status.reset_at.isoformat() if rate_status.reset_at else 'unknown'}"
            )
            await event_bus.emit(
                "heartbeat.rate_limited",
                {
                    "percent_used": rate_status.percent_used,
                    "reset_at": rate_status.reset_at.isoformat() if rate_status.reset_at else None,
                },
                entity_type="system",
            )
            return

        # 3. Schedule next task
        try:
            task_processed = await task_scheduler.schedule_next_task()
            if task_processed:
                logger.info("Heartbeat: processed a task")
            else:
                logger.debug("Heartbeat: no tasks to process")
        except Exception as e:
            logger.error(f"Task scheduling failed: {e}", exc_info=True)

    def is_running(self) -> bool:
        """Check if heartbeat is running."""
        return self._running


# Global heartbeat manager instance
heartbeat_manager = HeartbeatManager()
