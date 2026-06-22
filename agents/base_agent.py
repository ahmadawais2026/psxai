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
    GEMINI_MODEL,
    FALLBACK_BY_ROLE,
    GEN_CONFIG_BY_TIER,
    ROLE_TIER,
    USE_VERTEX,
    VERTEX_PROJECT,
    VERTEX_LOCATION,
    map_model_name,
)

# Default generation config when an agent has no role mapped (safety fallback).
DEFAULT_TIER = "reasoning"


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

    def __init__(self, name: str, persona: str, role: Optional[str] = None) -> None:
        self.name: str = name
        self.persona: str = persona
        # Role drives per-tier model selection + generation config (see config.py).
        self.role: Optional[str] = role

        if USE_VERTEX:
            self._log(f"Initializing GenAI Client on Vertex AI (Project: {VERTEX_PROJECT}, Location: {VERTEX_LOCATION})")
            self.client = genai.Client(
                vertexai=True,
                project=VERTEX_PROJECT,
                location=VERTEX_LOCATION
            )
        else:
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
        models, gen_cfg = self._resolve_routing()

        # Outer fallback loop: try each routed model in order. Each model runs its
        # own 3-attempt retry inside _invoke_once and *raises* on exhaustion; we
        # catch and advance to the next model. Only after every model fails do we
        # return the sentinel string (preserving the "query never raises" contract).
        last_error: Optional[Exception] = None
        for model in models:
            resolved = model
            if USE_VERTEX and "deepseek" not in model.lower():
                resolved = map_model_name(model)
            try:
                return self._invoke_once(resolved, gen_cfg, full_prompt)
            except Exception as exc:
                last_error = exc
                self._log(f"Model '{resolved}' failed; falling back to next routed model … ({exc})")

        self._log(f"All routed models failed. Last error: {last_error}")
        return (
            f"[{self.name}] Analysis unavailable — the AI service is "
            f"temporarily unreachable. Last error: {last_error}"
        )

    # ── Routing resolution ────────────────────────────────────────

    def _resolve_routing(self) -> tuple:
        """Resolve the ordered model list and generation config for this agent.

        An explicit ``self.model_name`` (set for internal A/B) overrides routing
        and forces a single model. Otherwise the role's fallback chain is used.
        Generation config is always keyed off the role's tier and held fixed
        across the whole fallback chain.
        """
        explicit = getattr(self, "model_name", None)
        role = getattr(self, "role", None)
        if explicit:
            models = [explicit]
        else:
            models = FALLBACK_BY_ROLE.get(role) or [GEMINI_MODEL]
        tier = ROLE_TIER.get(role, DEFAULT_TIER)
        gen_cfg = GEN_CONFIG_BY_TIER[tier]
        return models, gen_cfg

    # ── Single-model invocation (raises on exhaustion) ────────────

    def _invoke_once(self, model: str, gen_cfg: Dict[str, Any], full_prompt: str) -> str:
        """Invoke one model with its 3-attempt retry loop. Raises on exhaustion."""
        if "deepseek" in model.lower():
            return self._invoke_deepseek(model, gen_cfg, full_prompt)
        return self._invoke_gemini(model, gen_cfg, full_prompt)

    def _invoke_gemini(self, model: str, gen_cfg: Dict[str, Any], full_prompt: str) -> str:
        self._log(f"Querying Gemini model {model} ({len(full_prompt)} chars) …")

        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                # Build config from the resolved tier gen_cfg. The except branch
                # below reconstructs the SAME tier values minus thinking_config,
                # so a thinking_budget rejection never silently reverts to defaults.
                try:
                    response = self.client.models.generate_content(
                        model=model,
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=self.persona,
                            temperature=gen_cfg["temperature"],
                            max_output_tokens=gen_cfg["max_output_tokens"],
                            thinking_config=types.ThinkingConfig(
                                thinking_budget=gen_cfg["thinking_budget"]
                            ),
                        ),
                    )
                except Exception as config_exc:
                    if "thinking" in str(config_exc).lower() or "validation" in str(config_exc).lower():
                        self._log(f"Model {model} rejected thinking_config. Retrying without it...")
                        response = self.client.models.generate_content(
                            model=model,
                            contents=full_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=self.persona,
                                temperature=gen_cfg["temperature"],
                                max_output_tokens=gen_cfg["max_output_tokens"],
                            ),
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
                    self._log(f"Rate-limited (429). Waiting {wait}s (attempt {attempt}/3) …")
                    time.sleep(wait)
                    continue

                # Generic transient error: standard back-off.
                wait = 2 ** attempt  # 2 s, 4 s, 8 s
                self._log(f"Error: {err_str}. Retrying in {wait}s (attempt {attempt}/3) …")
                time.sleep(wait)

        raise RuntimeError(f"Gemini model {model} exhausted retries. Last error: {last_error}")

    def _invoke_deepseek(self, model: str, gen_cfg: Dict[str, Any], full_prompt: str) -> str:
        """DeepSeek (Vertex MaaS) path. Dormant on the free trial — no routing
        references a deepseek-* model — but retained for a future paid account."""
        import requests
        import google.auth
        import google.auth.transport.requests

        # The OpenAI-compatible endpoint expects the short "<publisher>/<model>" form.
        if "flash" in model.lower():
            vertex_model = "deepseek-ai/deepseek-v4-flash"
        else:
            vertex_model = "deepseek-ai/deepseek-v4-pro"

        self._log(f"Querying {vertex_model} via Vertex AI Agent Platform ({len(full_prompt)} chars) …")

        endpoint = (
            f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1beta1"
            f"/projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}"
            f"/endpoints/openapi/chat/completions"
        )

        payload = {
            "model": vertex_model,
            "messages": [
                {"role": "system", "content": self.persona},
                {"role": "user", "content": full_prompt},
            ],
            "temperature": gen_cfg["temperature"],
            "max_tokens": gen_cfg["max_output_tokens"],
        }
        # Only request max reasoning effort on the reasoning tier (thinking enabled).
        if gen_cfg.get("thinking_budget", 0) != 0:
            payload["reasoning_effort"] = "max"

        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                creds.refresh(google.auth.transport.requests.Request())

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {creds.token}",
                }

                response = requests.post(endpoint, json=payload, headers=headers, timeout=120)

                if response.status_code == 200:
                    res_json = response.json()
                    text = res_json["choices"][0]["message"]["content"].strip()
                    self._log(f"DeepSeek (Vertex) response received ({len(text)} chars).")
                    return text

                if response.status_code == 400 and "reasoning_effort" in payload:
                    self._log("reasoning_effort rejected, retrying without it …")
                    payload.pop("reasoning_effort", None)
                    continue

                err_text = response.text
                self._log(f"DeepSeek (Vertex) API error ({response.status_code}): {err_text}")
                last_error = RuntimeError(f"DeepSeek Vertex API error: {err_text}")

            except Exception as exc:
                last_error = exc
                self._log(f"DeepSeek (Vertex) query failed: {exc}")

            time.sleep(2 ** attempt)

        raise RuntimeError(f"DeepSeek model {vertex_model} exhausted retries. Last error: {last_error}")

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
