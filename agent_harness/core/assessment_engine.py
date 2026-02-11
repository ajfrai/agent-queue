"""Task assessment engine for analyzing complexity and requirements."""

import json
import logging
from anthropic import Anthropic

from ..config import config
from ..storage.models import AssessmentResult, Complexity

logger = logging.getLogger(__name__)


class AssessmentEngine:
    """Analyzes tasks to determine complexity and execution strategy."""

    def __init__(self):
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None
        self.model = config.ASSESSMENT_MODEL

    async def assess_task(self, title: str, description: str) -> AssessmentResult:
        """Assess a task's complexity and requirements."""
        if not self.client:
            logger.warning("No Anthropic API key configured, using default assessment")
            return self._default_assessment()

        try:
            prompt = self._build_assessment_prompt(title, description)

            message = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                temperature=0.0,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = message.content[0].text
            result = self._parse_assessment_response(response_text)
            return result

        except Exception as e:
            logger.error(f"Failed to assess task: {e}")
            return self._default_assessment()

    def _build_assessment_prompt(self, title: str, description: str) -> str:
        """Build the assessment prompt for Claude."""
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

Consider:
- File operations, API integrations, and complex logic increase complexity
- Well-specified tasks are simpler than vague ones
- Tasks requiring research or exploration are more complex
- Simple fixes or additions are usually "simple"
- Multi-file changes with testing are usually "medium"
- Architecture changes or new systems are usually "complex"

Respond ONLY with valid JSON, no additional text:"""

    def _parse_assessment_response(self, response_text: str) -> AssessmentResult:
        """Parse the Claude response into an AssessmentResult."""
        try:
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            data = json.loads(response_text.strip())

            return AssessmentResult(
                complexity=data.get("complexity", "medium"),
                recommended_model=data.get("recommended_model", "sonnet"),
                should_decompose=data.get("should_decompose", False),
                subtasks=data.get("subtasks", []),
                reasoning=data.get("reasoning", ""),
            )

        except Exception as e:
            logger.error(f"Failed to parse assessment response: {e}")
            logger.debug(f"Response text: {response_text}")
            return self._default_assessment()

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
