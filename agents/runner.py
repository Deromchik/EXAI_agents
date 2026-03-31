from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from state_machine import collect_planned_questions_for_phases

from . import prompts

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


def _format_history(messages: list[dict[str, str]], max_turns: int = 24) -> str:
    tail = messages[-max_turns:]
    lines = []
    for m in tail:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"No JSON object in model output: {text[:200]}...")


DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


def _openrouter_extra_headers() -> dict[str, str]:
    """Optional attribution headers for OpenRouter (https://openrouter.ai/docs)."""
    h: dict[str, str] = {}
    referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    title = os.getenv("OPENROUTER_X_TITLE", "").strip()
    if referer:
        h["HTTP-Referer"] = referer
    if title:
        h["X-Title"] = title
    return h


class AgentRunner:
    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.1,
        log_sink: list[dict[str, Any]] | None = None,
    ):
        self.model = (
            model
            or os.getenv("OPENROUTER_MODEL", "").strip()
            or os.getenv("OPENAI_MODEL", "").strip()
            or DEFAULT_OPENROUTER_MODEL
        )
        self.temperature = float(temperature)
        self._log_sink = log_sink
        self._client = None
        if OpenAI is not None:
            api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE).strip().rstrip("/")
            if api_key:
                self._client = OpenAI(base_url=base_url, api_key=api_key)

    def _append_log(
        self,
        agent_id: str,
        system_prompt: str,
        user_message: str,
        response_raw: str,
        response_parsed: Any | None = None,
    ) -> None:
        if self._log_sink is None:
            return
        entry: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "agent": agent_id,
            "model": self.model,
            "temperature": self.temperature,
            "input": {
                "system_prompt": system_prompt,
                "user_message": user_message,
            },
            "output_raw": response_raw,
        }
        if response_parsed is not None:
            entry["output_parsed"] = response_parsed
        self._log_sink.append(entry)

    def _complete(self, agent_id: str, system: str, user: str) -> str:
        if not self._client:
            raise RuntimeError(
                "LLM client unavailable: set OPENROUTER_API_KEY for OpenRouter."
            )
        extra_headers = _openrouter_extra_headers()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        r = self._client.chat.completions.create(**kwargs)
        raw = (r.choices[0].message.content or "").strip()
        self._append_log(agent_id, system, user, raw)
        return raw

    # --- Agents ----------------------------------------------------------

    def run_a14(self, block: dict, language_hint: str) -> str:
        roles = "\n".join(f"- {r}" for r in block["role_titles"])
        user = f"Language: {language_hint}\nBlock title: {block['title']}\nAudience: {block['audience']}\nRoles:\n{roles}"
        return self._complete("A14", prompts.SYSTEM_A14, user)

    def run_a15(self, language_hint: str) -> str:
        user = f"Language: {language_hint}\nAsk the user for their topic and background."
        return self._complete("A15", prompts.SYSTEM_A15, user)

    def run_a16(self, block: dict, user_answer: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        user = (
            f"Block: {block['title']}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            f"Latest user answer:\n{user_answer}"
        )
        raw = self._complete("A16", prompts.SYSTEM_A16, user)
        parsed = _extract_json_object(raw)
        if self._log_sink:
            self._log_sink[-1]["output_parsed"] = parsed
        return parsed

    def run_a13(self, messages: list[dict[str, str]], user_answer: str, language_hint: str) -> str:
        user = f"Language: {language_hint}\nConversation:\n{_format_history(messages)}\nUnclear answer:\n{user_answer}"
        return self._complete("A13", prompts.SYSTEM_A13, user)

    def run_a17(self, low_score_reason: str, messages: list[dict[str, str]], language_hint: str) -> str:
        user = (
            f"Language: {language_hint}\n"
            f"low_score_reason: {low_score_reason}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        return self._complete("A17", prompts.SYSTEM_A17, user)

    def run_a18(self, block: dict, a16_summary: dict[str, Any], messages: list[dict[str, str]], language_hint: str) -> str:
        phase_lines = "\n".join(
            f"{i + 1}. {ph['title']}" for i, ph in enumerate(block["phases"])
        )
        user = (
            f"Language: {language_hint}\n"
            f"Block: {block['title']}\n"
            f"Extracted focus: {a16_summary.get('extracted_focus_area', '')}\n"
            f"Phases:\n{phase_lines}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            "Propose scope and ask for confirmation."
        )
        return self._complete("A18", prompts.SYSTEM_A18, user)

    def run_a19(
        self,
        block: dict,
        user_answer: str,
        messages: list[dict[str, str]],
        language_hint: str,
    ) -> dict[str, Any]:
        phase_titles = [ph["title"] for ph in block["phases"]]
        user = (
            f"Language: {language_hint}\n"
            f"Block: {block['title']}\n"
            f"Phase titles: {phase_titles}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            f"User reply to scope proposal:\n{user_answer}"
        )
        raw = self._complete("A19", prompts.SYSTEM_A19, user)
        parsed = _extract_json_object(raw)
        if self._log_sink:
            self._log_sink[-1]["output_parsed"] = parsed
        return parsed

    def run_a20(self, suggested_modification: str, messages: list[dict[str, str]], language_hint: str) -> str:
        user = (
            f"Language: {language_hint}\n"
            f"suggested_modification: {suggested_modification}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        return self._complete("A20", prompts.SYSTEM_A20, user)

    def run_a21(
        self,
        block: dict,
        phase_indices: list[int],
        language_hint: str,
    ) -> str:
        planned = collect_planned_questions_for_phases(block, phase_indices)
        numbered = "\n".join(f"{i + 1}. {q}" for i, (_, _, q) in enumerate(planned))
        user = (
            f"Language: {language_hint}\n"
            f"Exact canonical questions to include verbatim:\n{numbered}\n"
            "Build the user-facing message per instructions."
        )
        return self._complete("A21", prompts.SYSTEM_A21, user)

    def run_a11(self, block: dict, messages: list[dict[str, str]], language_hint: str) -> str:
        user = f"Language: {language_hint}\nTopic: {block['title']}\nConversation:\n{_format_history(messages)}"
        return self._complete("A11", prompts.SYSTEM_A11, user)

    def run_a22(self, next_phase_title: str, language_hint: str) -> str:
        user = f"Language: {language_hint}\nNext phase title: {next_phase_title}"
        return self._complete("A22", prompts.SYSTEM_A22, user)

    def run_canonical_depth(
        self,
        canonical_question: str,
        user_answer: str,
        messages: list[dict[str, str]],
        language_hint: str,
    ) -> dict[str, Any]:
        user = (
            f"Language: {language_hint}\n"
            f"Canonical question (fixed):\n{canonical_question}\n"
            f"Conversation (excerpt):\n{_format_history(messages[-12:])}\n"
            f"User answer:\n{user_answer}"
        )
        raw = self._complete("CANONICAL_DEPTH", prompts.SYSTEM_CANONICAL_DEPTH, user)
        parsed = _extract_json_object(raw)
        if self._log_sink:
            self._log_sink[-1]["output_parsed"] = parsed
        return parsed
