"""
Base Agent — Foundation class for every AI agent in the advisory system.

Wraps the new Google Gen AI SDK to provide:
- Persona-driven system instructions
- Automatic retry with exponential back-off (rate-limit aware)
- Support for model thinking/reasoning configuration (high/max thinking level)
- JSON-mode querying with fence-stripping and validation
- Timestamped audit logging to console
"""

from __future__ import annotations

import json
import re
import time
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
)

# Maximum response length for the DeepSeek path. Without an explicit cap the
# API falls back to a short default, which truncates the granular output the
# personas ask for once thinking-mode reasoning eats into the budget.
DEEPSEEK_MAX_TOKENS = GEMINI_MAX_OUTPUT_TOKENS


class BaseAgent:
    """Base class for all Gemini-backed analyst agents.

    Parameters
    ----------
    name : str
        Human-readable agent name (used in log lines).
    persona : str
        System instruction that defines the agent's role and output format.
    """

    # ── Construction ──────────────────────────────────────────────

    def __init__(self, name: str, persona: str) -> None:
        self.name: str = name
        self.persona: str = persona

        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file or environment variables."
            )

        # Initialize the modern Google Gen AI Client
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    # ── Logging ───────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        """Print a timestamped, agent-prefixed log line to stdout."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [{self.name}] {message}")

    # ── Core query (text) ─────────────────────────────────────────

    def query(self, prompt: str, context: Optional[str] = None) -> str:
        """Send *prompt* to Gemini and return the text response.

        Parameters
        ----------
        prompt : str
            The user-turn message to send.
        context : str, optional
            Extra context prepended to the prompt (e.g. data dumps).

        Returns
        -------
        str
            The model's text response.

        Notes
        -----
        Retries up to 3 times with exponential back-off.  429 (rate-limit)
        errors receive an extra-long initial wait so the quota can replenish.
        """
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        model = getattr(self, "model_name", None) or GEMINI_MODEL

        if "deepseek" in model.lower():
            from config import DEEPSEEK_API_KEY
            self._log(f"Querying DeepSeek model {model} ({len(full_prompt)} chars) …")

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
            }

            payload = {
                "model": "deepseek-v4-pro",
                "messages": [
                    {"role": "system", "content": self.persona},
                    {"role": "user", "content": full_prompt}
                ],
                "temperature": GEMINI_TEMPERATURE,
                "max_tokens": DEEPSEEK_MAX_TOKENS
            }

            # Enable thinking/reasoning mode for DeepSeek V4Pro (Max)
            payload["extra_body"] = {
                "thinking": {
                    "type": "enabled"
                }
            }
            payload["reasoning_effort"] = "high"

            last_error: Optional[Exception] = None
            import requests
            for attempt in range(1, 4):
                try:
                    # DeepSeek thinking models can take up to 2 minutes under load
                    response = requests.post(
                        "https://api.deepseek.com/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=120
                    )

                    if response.status_code == 200:
                        res_json = response.json()
                        text = res_json["choices"][0]["message"]["content"].strip()
                        self._log(f"DeepSeek Response received ({len(text)} chars).")
                        return text

                    # Try falling back without thinking/extra_body if we get a 400 bad request
                    if response.status_code == 400 and "extra_body" in payload:
                        self._log("DeepSeek thinking mode parameters failed, retrying without thinking configuration...")
                        payload.pop("extra_body", None)
                        payload.pop("reasoning_effort", None)
                        continue

                    err_text = response.text
                    self._log(f"DeepSeek API error (status {response.status_code}): {err_text}")
                    last_error = RuntimeError(f"DeepSeek API error: {err_text}")

                except Exception as exc:
                    last_error = exc
                    self._log(f"DeepSeek query failed: {exc}")

                # Back-off before retry
                wait = 2 ** attempt
                time.sleep(wait)

            self._log(f"All DeepSeek attempts failed. Last error: {last_error}")
            return (
                f"[{self.name}] DeepSeek analysis unavailable. Last error: {last_error}"
            )

        else:
            self._log(f"Querying Gemini model {model} ({len(full_prompt)} chars) …")

            last_error: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    # Query with config enabling the high/max thinking level if supported
                    try:
                        response = self.client.models.generate_content(
                            model=model,
                            contents=full_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=self.persona,
                                temperature=GEMINI_TEMPERATURE,
                                max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                                thinking_config=types.ThinkingConfig(
                                    thinking_budget=-1  # Maximize reasoning budget
                                )
                            )
                        )
                    except Exception as config_exc:
                        # Fallback for models that do not support thinking_config
                        if "thinking" in str(config_exc).lower() or "validation" in str(config_exc).lower():
                            self._log(f"Model {model} doesn't support thinking_config. Retrying without it...")
                            response = self.client.models.generate_content(
                                model=model,
                                contents=full_prompt,
                                config=types.GenerateContentConfig(
                                    system_instruction=self.persona,
                                    temperature=GEMINI_TEMPERATURE,
                                    max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS
                                )
                            )
                        else:
                            raise config_exc

                    if response.text:
                        text = response.text.strip()
                        self._log(f"Response received ({len(text)} chars).")
                        return text

                    self._log("Empty response from model — retrying …")
                    last_error = RuntimeError("Model returned an empty response.")

                except Exception as exc:
                    last_error = exc
                    err_str = str(exc)

                    # Rate-limit: wait longer.
                    if "429" in err_str or "quota" in err_str.lower():
                        wait = 15 * attempt  # 15 s, 30 s, 45 s
                        self._log(
                            f"Rate-limited (429). Waiting {wait}s "
                            f"(attempt {attempt}/3) …"
                        )
                        time.sleep(wait)
                        continue

                    # Generic transient error: standard back-off.
                    wait = 2 ** attempt  # 2 s, 4 s, 8 s
                    self._log(
                        f"Error: {err_str}. Retrying in {wait}s "
                        f"(attempt {attempt}/3) …"
                    )
                    time.sleep(wait)

            # All attempts exhausted.
            self._log(f"All 3 attempts failed. Last error: {last_error}")
            return (
                f"[{self.name}] Analysis unavailable — the AI service is "
                f"temporarily unreachable. Last error: {last_error}"
            )

    # ── JSON query ────────────────────────────────────────────────

    def query_json(self, prompt: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Query Gemini and parse the response as JSON.

        The method strips Markdown code fences (```json … ```) before parsing
        and retries once with an explicit "return valid JSON" nudge if the
        first attempt is malformed.

        Parameters
        ----------
        prompt : str
            The user-turn message.
        context : str, optional
            Additional context string.

        Returns
        -------
        dict
            Parsed JSON object.
        """
        raw = self.query(prompt, context)

        # First attempt — try to parse directly.
        parsed = self._try_parse_json(raw)
        if parsed is not None:
            return parsed

        # Second attempt — ask the model to fix its own output.
        self._log("JSON parse failed. Requesting corrected JSON …")
        fix_prompt = (
            "Your previous response was not valid JSON. "
            "Return ONLY a valid JSON object — no markdown fences, "
            "no commentary, no trailing commas. "
            "Here is the text you produced:\n\n"
            f"{raw}"
        )
        raw_retry = self.query(fix_prompt)
        parsed = self._try_parse_json(raw_retry)
        if parsed is not None:
            return parsed

        # Fallback — return a minimal error dict so callers never crash.
        self._log("JSON retry also failed. Returning fallback error dict.")
        return {
            "error": True,
            "agent": self.name,
            "raw_response": raw[:2000],
            "summary": (
                f"{self.name} was unable to produce structured output. "
                "The raw text has been preserved for manual review."
            ),
        }

    # ── Private helpers ───────────────────────────────────────────

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove Markdown code fences that often wrap LLM JSON output."""
        pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _try_parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Attempt to parse *text* as JSON, returning ``None`` on failure."""
        cleaned = self._strip_code_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            self._log(f"JSON decode error: {exc}")
            return None
