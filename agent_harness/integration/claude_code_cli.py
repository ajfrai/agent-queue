"""Claude Code CLI integration via pexpect for terminal emulation."""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional, Callable, Awaitable

import pexpect

logger = logging.getLogger(__name__)

# Timeout for waiting on Claude CLI responses
DEFAULT_TIMEOUT = 600  # 10 minutes per turn (Claude can take a while)


class ClaudeCodeCLI:
    """Manages Claude Code CLI sessions via pexpect.

    Uses pexpect to spawn Claude Code in a pseudo-terminal, allowing
    proper interactive communication including slash commands and
    real-time output streaming.
    """

    def __init__(self):
        self.claude_bin = "claude"

    def spawn_session(
        self,
        task_description: str,
        working_directory: Path,
        model: str = "sonnet",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Optional[pexpect.spawn]:
        """Spawn a Claude Code CLI session with pexpect.

        Args:
            task_description: The task prompt for Claude
            working_directory: Working directory for the session
            model: Model to use (haiku, sonnet, opus)
            timeout: Timeout for expect operations

        Returns:
            The pexpect child process or None if failed
        """
        try:
            working_directory.mkdir(parents=True, exist_ok=True)

            # Build command args
            # claude -p runs in print (non-interactive) mode
            # For interactive sessions we pass the prompt directly
            cmd = self.claude_bin
            args = ["-p", task_description]

            # Add model flag if supported
            if model:
                args.extend(["--model", model])

            logger.info(f"Spawning Claude Code session in {working_directory}")
            logger.debug(f"Command: {cmd} {' '.join(args[:2])}...")

            # Build environment - inherit current env but ensure TERM is set
            env = os.environ.copy()
            env["TERM"] = "dumb"  # Avoid ANSI sequences

            child = pexpect.spawn(
                cmd,
                args=args,
                cwd=str(working_directory),
                encoding="utf-8",
                timeout=timeout,
                env=env,
                maxread=65536,
            )

            # Set larger window size so output isn't truncated
            child.setwinsize(200, 400)

            logger.info(f"Spawned Claude Code session with PID: {child.pid}")
            return child

        except Exception as e:
            logger.error(f"Failed to spawn Claude Code session: {e}")
            return None

    async def run_session(
        self,
        child: pexpect.spawn,
        stdout_path: Path,
        stderr_path: Path,
        on_output: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> int:
        """Monitor a pexpect session until completion.

        Reads output in a thread and writes to log files.
        Returns the exit code.
        """
        loop = asyncio.get_event_loop()

        def _run_sync():
            """Synchronous session monitoring."""
            with open(stdout_path, "w", buffering=1) as stdout_file:
                try:
                    while child.isalive():
                        try:
                            # Read a chunk of output
                            idx = child.expect(
                                [r"\n", pexpect.TIMEOUT, pexpect.EOF],
                                timeout=30,
                            )
                            if idx == 0:
                                line = (child.before or "") + "\n"
                                # Strip ANSI escape codes for log
                                clean_line = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line)
                                stdout_file.write(clean_line)
                                if on_output:
                                    # Schedule the async callback
                                    loop.call_soon_threadsafe(
                                        asyncio.ensure_future,
                                        on_output(clean_line)
                                    )
                            elif idx == 1:
                                # Timeout - check if still alive
                                continue
                            else:
                                # EOF
                                if child.before:
                                    remaining = child.before
                                    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", remaining)
                                    stdout_file.write(clean)
                                    if on_output:
                                        loop.call_soon_threadsafe(
                                            asyncio.ensure_future,
                                            on_output(clean)
                                        )
                                break
                        except pexpect.TIMEOUT:
                            continue
                        except pexpect.EOF:
                            break

                except Exception as e:
                    logger.error(f"Error in session monitoring: {e}")
                    with open(stderr_path, "a") as stderr_file:
                        stderr_file.write(f"Monitor error: {e}\n")

            child.close()
            return child.exitstatus or 0

        exit_code = await loop.run_in_executor(None, _run_sync)
        return exit_code

    def detect_turn_boundary(self, output_chunk: str) -> bool:
        """Heuristically detect turn boundaries in Claude Code output."""
        turn_indicators = [
            "Tool execution complete",
            "Waiting for user input",
            "Enter your message:",
            "claude>",
        ]
        return any(indicator in output_chunk for indicator in turn_indicators)

    async def send_input(self, child: pexpect.spawn, message: str):
        """Send input to a running Claude Code session."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, child.sendline, message
            )
            logger.info(f"Sent input to session: {message[:50]}...")
        except Exception as e:
            logger.error(f"Failed to send input to session: {e}")

    async def terminate_session(self, child: pexpect.spawn, timeout: int = 10) -> Optional[int]:
        """Gracefully terminate a session."""
        try:
            logger.info(f"Terminating session PID: {child.pid}")

            if child.isalive():
                child.sendline("/exit")
                try:
                    child.expect(pexpect.EOF, timeout=timeout)
                except pexpect.TIMEOUT:
                    logger.warning("Session didn't exit gracefully, terminating...")
                    child.terminate(force=True)

            child.close()
            logger.info(f"Session terminated with exit code: {child.exitstatus}")
            return child.exitstatus

        except Exception as e:
            logger.error(f"Failed to terminate session: {e}")
            return None


# Global CLI instance
claude_cli = ClaudeCodeCLI()
