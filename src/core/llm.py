"""
Gemini LLM client wrapper for structured extraction, scoring, and drafting.
Uses official google-genai SDK with Pydantic validation and resilient error handling.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Type, TypeVar
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

# Load environment variables from .env
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger("job_assistant.llm")

T = TypeVar("T", bound=BaseModel)


class GeminiClient:
    def __init__(self, model_name: str = "gemini-flash-latest"):
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        self.fallback_models = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        self._client = None

        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.info(f"Gemini client initialized with model {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize google.genai Client: {e}")
        else:
            logger.warning(
                "GEMINI_API_KEY not found in environment or .env. "
                "Running in offline heuristic / dry-run mode."
            )

    @property
    def is_available(self) -> bool:
        return self._client is not None and bool(self.api_key)

    def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """Generate text using Gemini with automatic fallback between flash models."""
        if not self.is_available:
            logger.warning("Gemini API key is not configured; cannot generate text via LLM.")
            return None

        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction,
        )

        models_to_try = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]
        for model in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                logger.warning(f"Gemini generate_text failed with {model}: {e}")
                continue

        logger.error("All Gemini models failed for generate_text.")
        return None

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_instruction: Optional[str] = None,
    ) -> Optional[T]:
        """
        Generate structured data validated against a Pydantic model.
        Uses JSON response schema with automatic model fallback.
        """
        if not self.is_available:
            logger.warning("Gemini API key is not configured; cannot perform structured LLM call.")
            return None

        from google.genai import types

        schema_json = json.dumps(response_model.model_json_schema())
        instruction = (
            f"{system_instruction or ''}\n"
            f"You must return ONLY a valid JSON object strictly matching this JSON schema:\n"
            f"{schema_json}\n"
            f"Do not include markdown fences (```json), commentary, or extra keys."
        )

        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            system_instruction=instruction,
        )

        models_to_try = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]
        for model in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )

                if not response or not response.text:
                    continue

                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                    raw_text = raw_text.strip()

                parsed = response_model.model_validate_json(raw_text)
                return parsed

            except ValidationError as ve:
                logger.error(f"Pydantic validation error with {model}: {ve}")
                continue
            except Exception as e:
                logger.warning(f"Gemini generate_structured failed with {model}: {e}")
                continue

        logger.error("All Gemini models failed for generate_structured.")
        return None


# Global singleton instance
_default_client: Optional[GeminiClient] = None


def get_llm_client() -> GeminiClient:
    global _default_client
    if _default_client is None:
        _default_client = GeminiClient()
    return _default_client
