"""Thin wrapper around the OpenAI SDK so business logic never imports it
directly. Only this module knows about the `openai` package.
"""

import logging
import os
from typing import Optional, Type, TypeVar

from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ResponseT = TypeVar("ResponseT", bound=BaseModel)

DEFAULT_MODEL = "gpt-4o-mini"
MAX_SCHEMA_RETRIES = 1  # one retry on a schema-invalid response, then give up


class AIAnalysisClient:
    """Calls OpenAI with a system prompt + user payload and parses the
    response into a typed pydantic model using Structured Outputs.
    Never raises on API failure or invalid output; returns None instead so
    callers can fail soft.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 30.0):
        self._client = OpenAI(api_key=api_key, timeout=timeout)
        self.model = model

    @classmethod
    def from_env(cls) -> Optional["AIAnalysisClient"]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set; AI analysis disabled")
            return None
        model = os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        return cls(api_key=api_key, model=model)

    def analyze(
        self,
        system_prompt: str,
        user_payload: str,
        response_model: Type[ResponseT],
    ) -> Optional[ResponseT]:
        """Calls the Responses API with Structured Outputs and returns the
        parsed pydantic model, or None if the call failed or the response
        could not be parsed after retrying once.
        """
        attempts = MAX_SCHEMA_RETRIES + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.responses.parse(
                    model=self.model,
                    instructions=system_prompt,
                    input=user_payload,
                    text_format=response_model,
                )
            except APITimeoutError as exc:
                logger.warning("OpenAI request timed out (attempt %d/%d): %s", attempt, attempts, exc)
                return None
            except RateLimitError as exc:
                logger.warning("OpenAI rate limit hit (attempt %d/%d): %s", attempt, attempts, exc)
                return None
            except APIError as exc:
                logger.warning("OpenAI API error (attempt %d/%d): %s", attempt, attempts, exc)
                return None
            except Exception as exc:  # network errors, unexpected SDK failures
                logger.warning(
                    "OpenAI call failed (attempt %d/%d): %s (%s)",
                    attempt, attempts, exc, type(exc).__name__,
                )
                return None

            parsed = response.output_parsed
            if parsed is not None:
                return parsed

            logger.warning(
                "OpenAI response missing/invalid structured output (attempt %d/%d): status=%s",
                attempt, attempts, getattr(response, "status", "unknown"),
            )

        logger.warning("OpenAI response invalid after %d attempt(s); giving up", attempts)
        return None
