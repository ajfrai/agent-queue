"""Task assessment engine for analyzing complexity and requirements."""

import json
import logging
from typing import List, Dict, Tuple
from anthropic import Anthropic

from ..config import config
from ..storage.models import AssessmentResult, Complexity

logger = logging.getLogger(__name__)


class AssessmentEngine:
    """Analyzes tasks to determine complexity and execution strategy."""

    def __init__(self):
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None
        self.model = config.ASSESSMENT_MODEL

    async def assess_batch(self, tasks: List[Tuple[int, str, str]]) -> Dict[int, AssessmentResult]:
        """Assess multiple tasks in a single LLM call.

        Args:
            tasks: List of (task_id, title, description) tuples.

        Returns:
            Dict mapping task_id to AssessmentResult.
        """
        if not tasks:
            return {}

        if not self.client:
            logger.warning("No Anthropic API key configured, using default assessment")
            return {tid: self._default_assessment() for tid, _, _ in tasks}

        # Single task — use simpler prompt
        if len(tasks) == 1:
            tid, title, desc = tasks[0]
            result = await self._assess_single(title, desc)
            return {tid: result}

        try:
            prompt = self._build_batch_prompt(tasks)

            message = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0.0,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = message.content[0].text
            return self._parse_batch_response(response_text, tasks)

        except Exception as e:
            logger.error(f"Failed to assess batch: {e}")
            return {tid: self._default_assessment() for tid, _, _ in tasks}

    async def _assess_single(self, title: str, description: str) -> AssessmentResult:
        """Assess a single task."""
        try:
            prompt = self._build_single_prompt(title, description)

            message = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                temperature=0.0,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = message.content[0].text
            return self._parse_single_response(response_text)

        except Exception as e:
            logger.error(f"Failed to assess task: {e}")
            return self._default_assessment()

    def _build_batch_prompt(self, tasks: List[Tuple[int, str, str]]) -> str:
        """Build a prompt that assesses multiple tasks at once."""
        task_blocks = []
        for tid, title, desc in tasks:
            task_blocks.append(f'  {{"id": {tid}, "title": {json.dumps(title)}, "description": {json.dumps(desc)}}}')
        tasks_json = "[\n" + ",\n".join(task_blocks) + "\n]"

        return f"""Assess each of the following coding tasks.

Tasks:
{tasks_json}

For EACH task, provide:
1. complexity: "simple", "medium", or "complex"
2. recommended_model: "haiku" (simple tasks), "sonnet" (most tasks), or "opus" (complex tasks)
3. should_decompose: boolean — almost always false (see rules below)
4. subtasks: array of strings — only if should_decompose is true
5. reasoning: short string explaining your assessment
6. comment: string or null — only include if you have a genuinely useful observation (a risk flag, clarifying question, dependency between tasks, or suggested approach). Do NOT comment just to acknowledge the task.

Rules:
- Simple fixes, additions, single-file changes → "simple"
- Multi-file changes with testing → "medium"
- Architecture changes or new systems → "complex"
- should_decompose must almost always be false
- Only decompose when the task CLEARLY requires multiple independent Claude Code sessions
- A single feature, bug fix, refactor, or multi-file change should NOT be decomposed
- When in doubt, do NOT decompose

Respond with a JSON array where each element has "id" plus the 6 fields above.
Respond ONLY with valid JSON, no additional text:"""

    def _build_single_prompt(self, title: str, description: str) -> str:
        """Build assessment prompt for a single task."""
        return f"""Analyze this coding task and provide an assessment.

Task Title: {title}

Task Description:
{description}

Please analyze this task and respond with a JSON object containing:
1. complexity: "simple", "medium", or "complex"
2. recommended_model: "haiku" (simple tasks), "sonnet" (most tasks), or "opus" (complex tasks)
3. should_decompose: boolean - whether this should be broken into subtasks
4. subtasks: array of strings - if decomposition recommended, list subtask titles
5. reasoning: string explaining your assessment
6. comment: string or null — only include if you have a genuinely useful observation (a risk flag, clarifying question, dependency, or suggested approach). Do NOT comment just to acknowledge the task.

CRITICAL — Decomposition bias:
- should_decompose should almost always be false
- Only set should_decompose=true when the task CLEARLY requires multiple independent Claude Code sessions
- A single feature, bug fix, refactor, or multi-file change should NOT be decomposed
- When in doubt, do NOT decompose — one Claude Code session can handle most tasks

Respond ONLY with valid JSON, no additional text:"""

    def _parse_batch_response(self, response_text: str, tasks: List[Tuple[int, str, str]]) -> Dict[int, AssessmentResult]:
        """Parse a batch assessment response."""
        try:
            response_text = self._strip_code_fences(response_text)
            data = json.loads(response_text)

            if not isinstance(data, list):
                logger.error("Batch response is not an array")
                return {tid: self._default_assessment() for tid, _, _ in tasks}

            results = {}
            for item in data:
                tid = item.get("id")
                if tid is not None:
                    results[tid] = AssessmentResult(
                        complexity=item.get("complexity", "medium"),
                        recommended_model=item.get("recommended_model", "sonnet"),
                        should_decompose=item.get("should_decompose", False),
                        subtasks=item.get("subtasks", []),
                        reasoning=item.get("reasoning", ""),
                        comment=item.get("comment"),
                    )

            # Fill in defaults for any tasks not in response
            for tid, _, _ in tasks:
                if tid not in results:
                    results[tid] = self._default_assessment()

            return results

        except Exception as e:
            logger.error(f"Failed to parse batch response: {e}")
            logger.debug(f"Response text: {response_text}")
            return {tid: self._default_assessment() for tid, _, _ in tasks}

    def _parse_single_response(self, response_text: str) -> AssessmentResult:
        """Parse a single task assessment response."""
        try:
            response_text = self._strip_code_fences(response_text)
            data = json.loads(response_text)

            return AssessmentResult(
                complexity=data.get("complexity", "medium"),
                recommended_model=data.get("recommended_model", "sonnet"),
                should_decompose=data.get("should_decompose", False),
                subtasks=data.get("subtasks", []),
                reasoning=data.get("reasoning", ""),
                comment=data.get("comment"),
            )

        except Exception as e:
            logger.error(f"Failed to parse assessment response: {e}")
            logger.debug(f"Response text: {response_text}")
            return self._default_assessment()

    def _strip_code_fences(self, text: str) -> str:
        """Strip markdown code fences from response."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def _default_assessment(self) -> AssessmentResult:
        """Return a conservative default assessment."""
        return AssessmentResult(
            complexity=Complexity.MEDIUM,
            recommended_model="sonnet",
            should_decompose=False,
            subtasks=[],
            reasoning="Default assessment (API unavailable or failed)",
        )


# Global assessment engine instance
assessment_engine = AssessmentEngine()
