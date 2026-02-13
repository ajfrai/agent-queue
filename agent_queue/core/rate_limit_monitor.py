"""Rate limit monitoring via Claude Code CLI probe.

Uses `claude -p --output-format json` with a minimal prompt as a probe.
Detects rate limits from error responses and stderr output.
Parses reset times from rate limit error messages.
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from ..config import config
from ..storage.models import RateLimitStatus
from ..storage.database import db

logger = logging.getLogger(__name__)

# Timeout for the probe command
PROBE_TIMEOUT = 30

# Minimum interval between probes (5 minutes)
PROBE_INTERVAL = timedelta(minutes=5)

# Varied probe messages to avoid repetitive sessions
_PROBE_MESSAGES = [
    "ok",
    "ping",
    "hi",
    "test",
    "1",
]

# Patterns that indicate rate limiting in Claude CLI output
RATE_LIMIT_PATTERNS = [
    re.compile(r"you.ve hit your limit", re.IGNORECASE),
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"usage limit", re.IGNORECASE),
    re.compile(r"exceeded.*quota", re.IGNORECASE),
    re.compile(r"capacity", re.IGNORECASE),
]

# Patterns to extract reset time from error messages
RESET_TIME_PATTERNS = [
    # "resets 8pm (America/New_York)"
    re.compile(r"resets?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*\(([^)]+)\)", re.IGNORECASE),
    # "resets at 2026-02-12T00:00:00"
    re.compile(r"resets?\s+(?:at\s+)?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)", re.IGNORECASE),
    # "try again in X minutes/hours"
    re.compile(r"try again in\s+(\d+)\s*(minutes?|hours?|mins?|hrs?)", re.IGNORECASE),
]


class RateLimitMonitor:
    """Monitor rate limits by probing the Claude Code CLI.

    Strategy:
    - Send a tiny probe: `claude -p --output-format json "ok"`
    - If it succeeds: we have capacity, not rate limited
    - If it fails with a rate limit error: parse reset time, mark as limited
    - Cache results and respect reset times to avoid wasteful probes
    """

    def __init__(self):
        self._cached_status: Optional[RateLimitStatus] = None
        self._last_probe: Optional[datetime] = None
        self._rate_limited_until: Optional[datetime] = None

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Check rate limit status via probe or cache.

        Probes at most once every PROBE_INTERVAL (5 minutes).
        Returns cached status between probes.
        """
        now = datetime.now(timezone.utc)

        # If we know we're rate limited and haven't hit the reset time, skip probe
        if self._rate_limited_until and now < self._rate_limited_until:
            logger.debug(
                f"Known rate limited until {self._rate_limited_until.isoformat()}, "
                f"skipping probe"
            )
            return self._cached_status or self._make_limited_status()

        # If rate limit window has passed, clear it
        if self._rate_limited_until and now >= self._rate_limited_until:
            logger.info("Rate limit window has passed, probing...")
            self._rate_limited_until = None

        # Respect probe interval — don't probe more often than every 5 minutes
        if self._last_probe and (now - self._last_probe) < PROBE_INTERVAL:
            if self._cached_status:
                return self._cached_status
            # No cache yet, fall through to probe

        try:
            result = await self._run_probe()
            status = self._interpret_probe_result(result)

            # Cache the result
            self._cached_status = status
            self._last_probe = now

            # Persist to database
            await self._cache_to_db(status, result.get("raw_output", ""))

            return status

        except Exception as e:
            logger.error(f"Probe failed: {e}")
            # Return cached or default
            cached = await db.get_rate_limit_status()
            return cached or self._default_status()

    async def _run_probe(self) -> Dict[str, Any]:
        """Run a minimal probe against the Claude CLI.

        Returns a dict with keys: success, json_output, stderr, exit_code, raw_output
        """
        try:
            # Ensure we use the subscription, never an API key
            import os
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)

            # Vary the probe message to avoid repetitive sessions
            probe_msg = random.choice(_PROBE_MESSAGES)

            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", "--output-format", "json",
                "--max-turns", "1",
                "--no-session-persistence",
                probe_msg,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=PROBE_TIMEOUT
            )

            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()
            exit_code = proc.returncode

            logger.debug(f"Probe exit={exit_code} stdout={stdout_str[:200]} stderr={stderr_str[:200]}")

            # Try to parse JSON from stdout
            json_output = None
            if stdout_str:
                try:
                    json_output = json.loads(stdout_str)
                except json.JSONDecodeError:
                    pass

            return {
                "success": exit_code == 0 and json_output is not None,
                "json_output": json_output,
                "stderr": stderr_str,
                "exit_code": exit_code,
                "raw_output": stdout_str + "\n" + stderr_str,
            }

        except asyncio.TimeoutError:
            logger.warning("Probe timed out")
            return {
                "success": False,
                "json_output": None,
                "stderr": "Probe timed out",
                "exit_code": -1,
                "raw_output": "Probe timed out",
            }

    def _interpret_probe_result(self, result: Dict[str, Any]) -> RateLimitStatus:
        """Interpret probe result to determine rate limit status."""
        now = datetime.now(timezone.utc)

        # Check stderr and stdout for rate limit indicators
        combined_output = result.get("raw_output", "")
        is_rate_limited = self._detect_rate_limit(combined_output)

        # Also check the JSON response for error indicators
        json_out = result.get("json_output")
        if json_out:
            if json_out.get("is_error"):
                error_result = json_out.get("result", "")
                if self._detect_rate_limit(error_result):
                    is_rate_limited = True

        if is_rate_limited:
            reset_at = self._parse_reset_time(combined_output)
            self._rate_limited_until = reset_at

            logger.warning(
                f"Rate limited! Reset at: {reset_at.isoformat() if reset_at else 'unknown'}"
            )

            return RateLimitStatus(
                tier="pro",
                messages_used=0,
                messages_limit=0,
                percent_used=100.0,
                is_limited=True,
                reset_at=reset_at,
                last_updated=now,
            )

        # Probe succeeded - extract usage info from the JSON response
        cost = 0.0
        if json_out:
            cost = json_out.get("total_cost_usd", 0.0)

        return RateLimitStatus(
            tier="pro",
            messages_used=0,
            messages_limit=0,
            percent_used=0.0,
            is_limited=False,
            last_updated=now,
        )

    def _detect_rate_limit(self, text: str) -> bool:
        """Check if text contains rate limit indicators."""
        for pattern in RATE_LIMIT_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def _parse_reset_time(self, text: str) -> Optional[datetime]:
        """Extract reset time from rate limit error message."""
        now = datetime.now(timezone.utc)

        for pattern in RESET_TIME_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue

            groups = match.groups()

            # Pattern: "resets 8pm (America/New_York)"
            if len(groups) == 2 and any(x in groups[0].lower() for x in ["am", "pm"]):
                try:
                    time_str = groups[0].strip()
                    # Parse the time
                    for fmt in ["%I%p", "%I:%M%p", "%I %p", "%I:%M %p"]:
                        try:
                            parsed_time = datetime.strptime(
                                time_str.upper().replace(" ", ""), fmt
                            )
                            # Combine with today's date
                            reset = now.replace(
                                hour=parsed_time.hour,
                                minute=parsed_time.minute,
                                second=0,
                                microsecond=0,
                            )
                            # If the time has passed today, it means tomorrow
                            if reset <= now:

                                reset += timedelta(days=1)
                            return reset
                        except ValueError:
                            continue
                except Exception:
                    pass

            # Pattern: ISO datetime
            if len(groups) == 1 and "-" in groups[0]:
                try:
                    return datetime.fromisoformat(
                        groups[0].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Pattern: "try again in X minutes/hours"
            if len(groups) == 2:
                try:
                    amount = int(groups[0])
                    unit = groups[1].lower()

                    if "hour" in unit or "hr" in unit:
                        return now + timedelta(hours=amount)
                    else:
                        return now + timedelta(minutes=amount)
                except (ValueError, TypeError):
                    pass

        # Default: assume reset in 1 hour if we can't parse

        return now + timedelta(hours=1)

    async def _cache_to_db(self, status: RateLimitStatus, raw_output: str):
        """Persist rate limit status to database."""
        try:
            await db.update_rate_limit_status({
                "tier": status.tier,
                "messages_used": status.messages_used,
                "messages_limit": status.messages_limit,
                "percent_used": status.percent_used,
                "reset_at": status.reset_at.isoformat() if status.reset_at else None,
                "raw_output": raw_output[:2000],  # Truncate to avoid bloat
            })
        except Exception as e:
            logger.error(f"Failed to cache rate limit status: {e}")

    def _make_limited_status(self) -> RateLimitStatus:
        """Create a rate-limited status with cached reset time."""
        return RateLimitStatus(
            tier="pro",
            messages_used=0,
            messages_limit=0,
            percent_used=100.0,
            is_limited=True,
            reset_at=self._rate_limited_until,
            last_updated=datetime.now(timezone.utc),
        )

    def _default_status(self) -> RateLimitStatus:
        """Conservative default when we can't determine status."""
        return RateLimitStatus(
            tier="unknown",
            messages_used=0,
            messages_limit=0,
            percent_used=0.0,
            is_limited=False,
            last_updated=datetime.now(timezone.utc),
        )

    async def is_rate_limited(self) -> bool:
        """Check if we're currently rate limited."""
        status = await self.get_rate_limit_status()
        return status.is_limited

    def mark_rate_limited(self, reset_at: Optional[datetime] = None):
        """Externally mark as rate limited (e.g., from a session failure)."""

        now = datetime.now(timezone.utc)
        self._rate_limited_until = reset_at or (now + timedelta(hours=1))
        self._cached_status = self._make_limited_status()
        logger.warning(f"Externally marked rate limited until {self._rate_limited_until}")


# Global rate limit monitor instance
rate_limit_monitor = RateLimitMonitor()
