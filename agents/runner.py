from __future__ import annotations

import json
import os
import re
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
    def __init__(self, mock: bool | None = None, model: str | None = None):
        if mock is None:
            mock = os.getenv("EXAI_MOCK_LLM", "0").lower() in ("1", "true", "yes")
        self.mock = mock
        self.model = (
            model
            or os.getenv("OPENROUTER_MODEL", "").strip()
            or os.getenv("OPENAI_MODEL", "").strip()
            or DEFAULT_OPENROUTER_MODEL
        )
        self._client = None
        if not self.mock and OpenAI is not None:
            api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE).strip().rstrip("/")
            if api_key:
                self._client = OpenAI(base_url=base_url, api_key=api_key)
            else:
                self._client = None

    def _complete(self, system: str, user: str) -> str:
        if self.mock:
            return ""
        if not self._client:
            raise RuntimeError(
                "LLM client unavailable: set OPENROUTER_API_KEY (OpenRouter) "
                "or enable EXAI_MOCK_LLM=1 / Mock LLM in the sidebar."
            )
        extra_headers = _openrouter_extra_headers()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
        }
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        r = self._client.chat.completions.create(**kwargs)
        return (r.choices[0].message.content or "").strip()

    # --- Mock heuristics -------------------------------------------------

    def _mock_a16(self, user_answer: str) -> dict[str, Any]:
        long_enough = len(user_answer.strip()) > 40
        return {
            "answer_understanding_score": 0.85 if long_enough else 0.55,
            "purpose_understanding_score": 0.95 if long_enough else 0.75,
            "extracted_focus_area": user_answer[:120] or "unclear",
            "focus_specificity_score": 0.95 if long_enough else 0.80,
            "low_score_reason": "" if long_enough else "Need more specific focus.",
        }

    def _mock_a19(self, user_answer: str, phase_titles: list[str]) -> dict[str, Any]:
        agree = "no" not in user_answer.lower() and len(user_answer.strip()) > 5
        return {
            "answer_understanding_score": 0.9,
            "scope_agreement_score": 0.95 if agree else 0.75,
            "scope_areas": phase_titles,
            "negotiation_needed": not agree,
            "suggested_modification": "" if agree else "Clarify which phases to include.",
        }

    def _mock_depth(self, user_answer: str) -> dict[str, Any]:
        deep = len(user_answer.strip()) > 60
        return {
            "deep_knowledge_level": 0.85 if deep else 0.55,
            "should_reask": 0 if deep else 1,
            "follow_up_question": ""
            if deep
            else "Could you add one concrete example or metric from your practice?",
            "low_score_reason": "" if deep else "Answer lacks concrete detail.",
        }

    # --- Agents ----------------------------------------------------------

    def run_a14(self, block: dict, language_hint: str) -> str:
        roles = "\n".join(f"- {r}" for r in block["role_titles"])
        user = f"Language: {language_hint}\nBlock title: {block['title']}\nAudience: {block['audience']}\nRoles:\n{roles}"
        if self.mock:
            return (
                f"Welcome. Today we focus on «{block['title']}» ({block['audience']}). "
                f"We will align on your focus, then go through the structured phases. "
                f"Could you briefly describe your role and experience in this area?"
            )
        return self._complete(prompts.SYSTEM_A14, user)

    def run_a15(self, language_hint: str) -> str:
        user = f"Language: {language_hint}\nAsk the user for their topic and background."
        if self.mock:
            return (
                "Welcome. Which expert area should we explore today, "
                "and what is your background in that field?"
            )
        return self._complete(prompts.SYSTEM_A15, user)

    def run_a16(self, block: dict, user_answer: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        if self.mock:
            return self._mock_a16(user_answer)
        user = (
            f"Block: {block['title']}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            f"Latest user answer:\n{user_answer}"
        )
        raw = self._complete(prompts.SYSTEM_A16, user)
        return _extract_json_object(raw)

    def run_a13(self, messages: list[dict[str, str]], user_answer: str, language_hint: str) -> str:
        user = f"Language: {language_hint}\nConversation:\n{_format_history(messages)}\nUnclear answer:\n{user_answer}"
        if self.mock:
            return (
                "I want to make sure I understood — could you rephrase your last point "
                "in a bit more detail?"
            )
        return self._complete(prompts.SYSTEM_A13, user)

    def run_a17(self, low_score_reason: str, messages: list[dict[str, str]], language_hint: str) -> str:
        user = (
            f"Language: {language_hint}\n"
            f"low_score_reason: {low_score_reason}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        if self.mock:
            return (
                "Could you narrow down which part of this domain you work in most "
                "and what outcomes you optimize for?"
            )
        return self._complete(prompts.SYSTEM_A17, user)

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
        if self.mock:
            return (
                f"I suggest we cover these phases in order:\n{phase_lines}\n"
                f"Does this plan work for you, or should we adjust?"
            )
        return self._complete(prompts.SYSTEM_A18, user)

    def run_a19(
        self,
        block: dict,
        user_answer: str,
        messages: list[dict[str, str]],
        language_hint: str,
    ) -> dict[str, Any]:
        phase_titles = [ph["title"] for ph in block["phases"]]
        if self.mock:
            return self._mock_a19(user_answer, phase_titles)
        user = (
            f"Language: {language_hint}\n"
            f"Block: {block['title']}\n"
            f"Phase titles: {phase_titles}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            f"User reply to scope proposal:\n{user_answer}"
        )
        raw = self._complete(prompts.SYSTEM_A19, user)
        return _extract_json_object(raw)

    def run_a20(self, suggested_modification: str, messages: list[dict[str, str]], language_hint: str) -> str:
        user = (
            f"Language: {language_hint}\n"
            f"suggested_modification: {suggested_modification}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        if self.mock:
            return (
                "Which phases should we prioritize or skip so the session matches your expectations?"
            )
        return self._complete(prompts.SYSTEM_A20, user)

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
        if self.mock:
            return (
                "Below are the exact research questions we will ask (verbatim). "
                "When you are ready, continue and we will go through them one by one.\n\n"
                f"{numbered}\n\n"
                "Please confirm when you are ready to begin."
            )
        return self._complete(prompts.SYSTEM_A21, user)

    def run_a11(self, block: dict, messages: list[dict[str, str]], language_hint: str) -> str:
        user = f"Language: {language_hint}\nTopic: {block['title']}\nConversation:\n{_format_history(messages)}"
        if self.mock:
            return (
                "Thank you for the detailed conversation. We will prepare the summary materials. "
                "Have a productive day."
            )
        return self._complete(prompts.SYSTEM_A11, user)

    def run_a22(self, next_phase_title: str, language_hint: str) -> str:
        user = f"Language: {language_hint}\nNext phase title: {next_phase_title}"
        if self.mock:
            return f"Moving on to: {next_phase_title}."
        return self._complete(prompts.SYSTEM_A22, user)

    def run_canonical_depth(
        self,
        canonical_question: str,
        user_answer: str,
        messages: list[dict[str, str]],
        language_hint: str,
    ) -> dict[str, Any]:
        if self.mock:
            return self._mock_depth(user_answer)
        user = (
            f"Language: {language_hint}\n"
            f"Canonical question (fixed):\n{canonical_question}\n"
            f"Conversation (excerpt):\n{_format_history(messages[-12:])}\n"
            f"User answer:\n{user_answer}"
        )
        raw = self._complete(prompts.SYSTEM_CANONICAL_DEPTH, user)
        return _extract_json_object(raw)
