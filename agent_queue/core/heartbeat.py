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
    """Manages the heartbeat loop that coordinates task execution.

    2-phase cycle:
      Odd beats:  assess — batch-assess unassessed tasks (with optional comments)
      Even beats: execute — run next N assessed tasks in parallel

    Every 10th beat: garbage-collect stale worktrees.
    """

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.last_beat: Optional[datetime] = None
        self.last_rate_status = None  # Cached for UI reads
        self.beat_count = 0

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

    async def _beat(self) -> dict:
        """Execute a single heartbeat cycle. Returns diagnostic info.

        2-phase cycle:
          odd beats:  assess — batch-assess unassessed tasks (comments folded in)
          even beats: execute — run next N assessed tasks in parallel
        """
        self.beat_count += 1
        phase_idx = self.beat_count % 2
        phase = {1: "assess", 0: "execute"}[phase_idx]

        diag = {"timestamp": None, "rate_limited": None, "rate_error": None,
                "beat_number": self.beat_count, "phase": phase}
        self.last_beat = datetime.utcnow()
        diag["timestamp"] = self.last_beat.isoformat()

        # 1. Check rate limits - never let this crash the heartbeat
        rate_status = None
        try:
            rate_status = await rate_limit_monitor.get_rate_limit_status()
            self.last_rate_status = rate_status
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            diag["rate_error"] = str(e)

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
            diag["rate_limited"] = rate_status.is_limited

        # Emit heartbeat event
        await event_bus.emit(
            "heartbeat.tick",
            {
                "timestamp": self.last_beat.isoformat(),
                "rate_limit": rate_payload,
                "beat_number": self.beat_count,
                "phase": phase,
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
            return diag

        # 3. Dedupe on every beat
        try:
            dupes_removed = await task_scheduler.dedupe_tasks()
            diag["dupes_removed"] = dupes_removed
        except Exception as e:
            logger.error(f"Task dedup failed: {e}", exc_info=True)

        # 4. Phase action
        if phase == "assess":
            try:
                assessed = await task_scheduler.assess_pending_tasks()
                diag["tasks_assessed"] = assessed
                if assessed:
                    logger.info(f"Heartbeat #{self.beat_count}: assessed {assessed} task(s)")
                else:
                    logger.debug(f"Heartbeat #{self.beat_count}: no tasks to assess")
            except Exception as e:
                logger.error(f"Assessment failed: {e}", exc_info=True)
                diag["assess_error"] = str(e)

        elif phase == "execute":
            try:
                executed = await task_scheduler.execute_next_tasks()
                diag["tasks_executed"] = executed
                if executed:
                    logger.info(f"Heartbeat #{self.beat_count}: executed/checked {executed} task(s)")
                else:
                    logger.debug(f"Heartbeat #{self.beat_count}: no tasks to execute")
            except Exception as e:
                logger.error(f"Execution failed: {e}", exc_info=True)
                diag["execute_error"] = str(e)

        # 5. Periodic garbage collection (every 10th beat)
        if self.beat_count % 10 == 0:
            try:
                await task_scheduler.cleanup_stale_worktrees()
                logger.debug(f"Heartbeat #{self.beat_count}: GC pass completed")
            except Exception as e:
                logger.error(f"Worktree GC failed: {e}", exc_info=True)

        return diag

    async def trigger(self) -> dict:
        """Manually trigger a single heartbeat cycle. Returns diagnostic info."""
        logger.info("Manual heartbeat triggered")
        return await self._beat()

    def is_running(self) -> bool:
        """Check if heartbeat is running."""
        return self._running


# Global heartbeat manager instance
heartbeat_manager = HeartbeatManager()
