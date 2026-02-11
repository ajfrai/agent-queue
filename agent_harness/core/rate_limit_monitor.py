"""Rate limit monitoring via Claude Code CLI /usage command."""

import asyncio
import logging
import re
from datetime import datetime
from functools import partial
from typing import Optional, Dict, Any

import pexpect

from ..config import config
from ..storage.models import RateLimitStatus
from ..storage.database import db

logger = logging.getLogger(__name__)

# How long to wait for Claude CLI to produce output
PEXPECT_TIMEOUT = 30


class RateLimitMonitor:
    """Monitor rate limits by running /usage in a Claude Code CLI session."""

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Spawn Claude CLI, send /usage, parse output."""
        try:
            # Run pexpect in a thread since it's synchronous
            loop = asyncio.get_event_loop()
            raw_output = await loop.run_in_executor(
                None, self._query_usage_sync
            )

            if raw_output is None:
                logger.warning("Failed to get /usage output, returning cached or default")
                cached = await db.get_rate_limit_status()
                return cached or self._default_status()

            # Parse the output
            parsed = self._parse_usage_output(raw_output)

            status = RateLimitStatus(
                tier=parsed.get("tier"),
                messages_used=parsed.get("messages_used", 0),
                messages_limit=parsed.get("messages_limit", 0),
                percent_used=parsed.get("percent_used", 0.0),
                is_limited=parsed.get("percent_used", 0.0) >= 90.0,
                reset_at=parsed.get("reset_at"),
                last_updated=datetime.utcnow(),
            )

            # Cache in database
            await db.update_rate_limit_status({
                "tier": status.tier,
                "messages_used": status.messages_used,
                "messages_limit": status.messages_limit,
                "percent_used": status.percent_used,
                "reset_at": status.reset_at.isoformat() if status.reset_at else None,
                "raw_output": raw_output,
            })

            return status

        except Exception as e:
            logger.error(f"Failed to get rate limit status: {e}")
            cached = await db.get_rate_limit_status()
            return cached or self._default_status()

    def _query_usage_sync(self) -> Optional[str]:
        """Synchronously spawn claude CLI, send /usage, capture output."""
        child = None
        try:
            # Spawn claude in interactive mode
            child = pexpect.spawn(
                "claude",
                encoding="utf-8",
                timeout=PEXPECT_TIMEOUT,
                env={"TERM": "dumb", "PATH": "/usr/local/bin:/usr/bin:/bin"},
            )

            # Wait for the initial prompt (Claude shows a prompt when ready)
            # The prompt varies, so we wait for any settling of output
            child.expect([r".*\$", r".*>", pexpect.TIMEOUT], timeout=15)

            # Send /usage command
            child.sendline("/usage")

            # Capture everything until we see another prompt or timeout
            # /usage output typically contains "Usage" and percentage info
            output_chunks = []
            try:
                while True:
                    idx = child.expect(
                        [r"\n", pexpect.TIMEOUT, pexpect.EOF],
                        timeout=10
                    )
                    if idx == 0:
                        output_chunks.append(child.before + "\n")
                    else:
                        # Timeout or EOF - we likely have all the output
                        if child.before:
                            output_chunks.append(child.before)
                        break
            except pexpect.EOF:
                if child.before:
                    output_chunks.append(child.before)

            raw_output = "".join(output_chunks)
            logger.info(f"/usage output captured ({len(raw_output)} chars)")
            logger.debug(f"/usage raw output: {raw_output!r}")

            return raw_output

        except pexpect.ExceptionPexpect as e:
            logger.error(f"pexpect error querying /usage: {e}")
            return None
        except Exception as e:
            logger.error(f"Error querying /usage: {e}")
            return None
        finally:
            if child and child.isalive():
                try:
                    child.sendline("/exit")
                    child.expect(pexpect.EOF, timeout=5)
                except Exception:
                    child.terminate(force=True)

    def _parse_usage_output(self, raw_output: str) -> Dict[str, Any]:
        """Parse the /usage command output to extract rate limit info.

        The /usage output format varies but typically contains lines like:
        - "Plan: Pro" or "Tier: pro"
        - "Messages used: 42/500" or "42 of 500 messages used"
        - "Usage: 8.4%" or "8.4% of daily limit"
        - "Resets at: 2026-02-12 00:00:00 UTC"
        """
        result: Dict[str, Any] = {}

        # Normalize: strip ANSI escape codes
        clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_output)

        # Extract tier/plan
        tier_match = re.search(r"(?:plan|tier)[:\s]+(\w+)", clean, re.IGNORECASE)
        if tier_match:
            result["tier"] = tier_match.group(1).lower()

        # Extract messages used/limit - try various formats
        # Format: "42/500" or "42 of 500" or "42 / 500"
        msg_match = re.search(
            r"(\d+)\s*(?:/|of)\s*(\d+)\s*(?:messages|msg)?",
            clean, re.IGNORECASE
        )
        if msg_match:
            result["messages_used"] = int(msg_match.group(1))
            result["messages_limit"] = int(msg_match.group(2))

        # Extract percentage
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", clean)
        if pct_match:
            result["percent_used"] = float(pct_match.group(1))
        elif "messages_used" in result and "messages_limit" in result:
            # Calculate from messages if not explicitly shown
            if result["messages_limit"] > 0:
                result["percent_used"] = (
                    result["messages_used"] / result["messages_limit"] * 100
                )

        # Extract reset time
        reset_match = re.search(
            r"reset[s]?\s*(?:at|:)\s*(.+?)(?:\n|$)",
            clean, re.IGNORECASE
        )
        if reset_match:
            try:
                reset_str = reset_match.group(1).strip()
                result["reset_at"] = datetime.fromisoformat(
                    reset_str.replace(" UTC", "+00:00").replace("Z", "+00:00")
                )
            except ValueError:
                logger.debug(f"Could not parse reset time: {reset_match.group(1)}")

        return result

    async def is_rate_limited(self) -> bool:
        """Check if we're currently rate limited."""
        status = await self.get_rate_limit_status()
        return status.is_limited

    def _default_status(self) -> RateLimitStatus:
        """Conservative default when we can't query usage."""
        return RateLimitStatus(
            tier="unknown",
            messages_used=0,
            messages_limit=0,
            percent_used=0.0,
            is_limited=False,
            last_updated=datetime.utcnow(),
        )


# Global rate limit monitor instance
rate_limit_monitor = RateLimitMonitor()
