"""Claude Code CLI integration via subprocess.

Uses `claude -p --output-format stream-json` for task execution and
`claude -p --output-format json` for single-shot operations.
No pexpect needed - just clean subprocess management.
"""

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Optional, Callable, Awaitable, Dict, Any

logger = logging.getLogger(__name__)

# Timeout for waiting on Claude CLI responses (10 minutes per task)
DEFAULT_TIMEOUT = 600


class ClaudeCodeCLI:
    """Manages Claude Code CLI sessions via subprocess.

    Uses claude -p (print mode) with --output-format stream-json
    for real-time streaming output with structured JSON events.
    """

    def __init__(self):
        self.claude_bin = "claude"

    async def run_task(
        self,
        task_description: str,
        working_directory: Path,
        model: str = "sonnet",
        stdout_path: Optional[Path] = None,
        stderr_path: Optional[Path] = None,
        on_output: Optional[Callable[[str], Awaitable[None]]] = None,
        on_json_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """Run a task using claude -p --output-format stream-json.

        Args:
            task_description: The task prompt for Claude
            working_directory: Working directory for the session
            model: Model to use (haiku, sonnet, opus)
            stdout_path: Path to write raw stdout
            stderr_path: Path to write stderr
            on_output: Async callback for each text chunk
            on_json_event: Async callback for each JSON event
            timeout: Total timeout for the task

        Returns:
            Dict with keys: exit_code, result_json, is_rate_limited, error
        """
        working_directory.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.claude_bin,
            "-p",
            "--verbose",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]

        if model:
            cmd.extend(["--model", model])

        cmd.append(task_description)

        logger.info(f"Running task in {working_directory} with model={model}")

        env = os.environ.copy()
        # Ensure we use the subscription, never an API key
        env.pop("ANTHROPIC_API_KEY", None)
        pid = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(working_directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            pid = proc.pid
            logger.info(f"Started Claude CLI with PID {pid}")

            # Open log files
            stdout_file = open(stdout_path, "w") if stdout_path else None
            stderr_file = open(stderr_path, "w") if stderr_path else None

            result_json = None
            is_rate_limited = False
            rate_limit_text = ""

            try:
                async def read_stdout():
                    nonlocal result_json, is_rate_limited, rate_limit_text
                    while True:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=timeout
                        )
                        if not line:
                            break

                        line_str = line.decode("utf-8", errors="replace")

                        if stdout_file:
                            stdout_file.write(line_str)
                            stdout_file.flush()

                        stripped = line_str.strip()
                        if stripped:
                            try:
                                event = json.loads(stripped)

                                if event.get("type") == "result":
                                    result_json = event

                                if on_json_event:
                                    await on_json_event(event)

                                if on_output:
                                    text = self._extract_text(event)
                                    if text:
                                        await on_output(text)

                            except json.JSONDecodeError:
                                if on_output:
                                    await on_output(stripped + "\n")

                                if self._is_rate_limit_text(stripped):
                                    is_rate_limited = True
                                    rate_limit_text = stripped

                async def read_stderr():
                    nonlocal is_rate_limited, rate_limit_text
                    data = await proc.stderr.read()
                    if data:
                        stderr_str = data.decode("utf-8", errors="replace")
                        if stderr_file:
                            stderr_file.write(stderr_str)
                        if self._is_rate_limit_text(stderr_str):
                            is_rate_limited = True
                            rate_limit_text = stderr_str

                await asyncio.gather(read_stdout(), read_stderr())
                exit_code = await proc.wait()

            finally:
                if stdout_file:
                    stdout_file.close()
                if stderr_file:
                    stderr_file.close()

            # Check result_json for rate limit errors
            if result_json and result_json.get("is_error"):
                error_text = result_json.get("result", "")
                if self._is_rate_limit_text(error_text):
                    is_rate_limited = True
                    rate_limit_text = error_text

            return {
                "exit_code": exit_code,
                "pid": pid,
                "result_json": result_json,
                "is_rate_limited": is_rate_limited,
                "rate_limit_text": rate_limit_text,
                "error": None,
            }

        except asyncio.TimeoutError:
            logger.error(f"Task timed out after {timeout}s")
            if proc and proc.returncode is None:
                proc.terminate()
                await asyncio.sleep(2)
                if proc.returncode is None:
                    proc.kill()
            return {
                "exit_code": -1,
                "pid": pid,
                "result_json": None,
                "is_rate_limited": False,
                "rate_limit_text": "",
                "error": f"Timed out after {timeout}s",
            }
        except Exception as e:
            logger.error(f"Failed to run task: {e}")
            return {
                "exit_code": -1,
                "pid": None,
                "result_json": None,
                "is_rate_limited": False,
                "rate_limit_text": "",
                "error": str(e),
            }

    def _extract_text(self, event: Dict[str, Any]) -> Optional[str]:
        """Extract displayable text from a stream-json event."""
        event_type = event.get("type", "")

        if event_type == "assistant":
            message = event.get("message", {})
            content = message.get("content", [])
            texts = []
            for block in content:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
            return "\n".join(texts) if texts else None

        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")

        if event_type == "result":
            return event.get("result", "")

        return None

    def _is_rate_limit_text(self, text: str) -> bool:
        """Check if text indicates a rate limit."""
        patterns = [
            "you've hit your limit",
            "rate limit",
            "too many requests",
            "usage limit",
            "exceeded",
        ]
        text_lower = text.lower()
        return any(p in text_lower for p in patterns)

    async def terminate_process(self, pid: int, timeout: int = 10):
        """Terminate a running process by PID."""
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(timeout)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.error(f"Failed to terminate PID {pid}: {e}")


# Global CLI instance
claude_cli = ClaudeCodeCLI()
