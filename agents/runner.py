from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from . import prompts
from state_machine import STEP_ORDER

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


def _phase_lines_for_prompt(block: dict) -> str:
    """Numbered list with phase_id for internal evaluators / JSON agents only (not user-facing copy)."""
    lines: list[str] = []
    for i, ph in enumerate(block["phases"]):
        pid = ph.get("phase_id")
        label = f"phase_id={pid!r}" if pid is not None else f"phase_index={i}"
        lines.append(f"{i + 1}. [{label}] {ph['title']}")
    return "\n".join(lines)


def _phase_human_numbered_titles(block: dict) -> str:
    """Ordinal + title only — what the user should see (no phase_id strings)."""
    return "\n".join(f"{i + 1}. {ph['title']}" for i, ph in enumerate(block["phases"]))


def _phase_map_for_prompt(block: dict) -> str:
    """Compact phase_id ↔ title map for JSON evaluators."""
    pairs = []
    for i, ph in enumerate(block["phases"]):
        pairs.append(
            {
                "phase_id": ph.get("phase_id", i),
                "title": ph["title"],
            }
        )
    return json.dumps(pairs, ensure_ascii=False)


def _format_history(messages: list[dict[str, str]], max_turns: int = 24) -> str:
    tail = messages[-max_turns:]
    lines = []
    for m in tail:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


def _user_messages_for_specialty_context(
    messages: list[dict[str, str]],
    *,
    max_turns: int = 48,
    max_total_chars: int = 8000,
) -> str:
    """Chronological user-only text for anchoring questions to stated role / experience."""
    if not messages:
        return "(no user messages yet)"
    tail = messages[-max_turns:]
    parts: list[str] = []
    total = 0
    for m in tail:
        if m.get("role") != "user":
            continue
        t = (m.get("content") or "").strip()
        if not t:
            continue
        if total + len(t) > max_total_chars:
            room = max_total_chars - total - 40
            if room > 200:
                parts.append(t[:room] + "…")
            else:
                parts.append("… [user turn too long; truncated]")
            break
        parts.append(t)
        total += len(t) + 4
    return "\n---\n".join(parts) if parts else "(no user messages yet)"


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

# Agents that return JSON with string fields — enforce language on those strings too.
_JSON_LANGUAGE_AGENTS = frozenset({"A16", "A19", "CANONICAL_DEPTH"})


def _apply_response_language(system: str, language: str, agent_id: str) -> str:
    """Append strict output-language rules (similar in spirit to languagePrompt in system_prompt_builder)."""
    lang = (language or "English").strip() or "English"
    block = (
        f"\n\n---\n## Response language (mandatory)\n"
        f"Your entire assistant output MUST be written ONLY in **{lang}**. "
        f"No other languages; do not mix languages."
    )
    if agent_id in _JSON_LANGUAGE_AGENTS:
        block += (
            f"\nFor the JSON object, every string value (including extracted_focus_area, "
            f"extended_focus_area, low_score_reason, follow_up_question, scope_areas entries, "
            f"suggested_modification, exception_knowledge if present) MUST be in **{lang}**. "
            f"JSON keys stay in English; numeric/boolean types unchanged."
        )
    return system + block


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
        response_language: str,
        response_parsed: Any | None = None,
    ) -> None:
        if self._log_sink is None:
            return
        entry: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "agent": agent_id,
            "model": self.model,
            "temperature": self.temperature,
            "response_language": response_language,
            "input": {
                "system_prompt": system_prompt,
                "user_message": user_message,
            },
            "output_raw": response_raw,
        }
        if response_parsed is not None:
            entry["output_parsed"] = response_parsed
        self._log_sink.append(entry)

    def _complete(self, agent_id: str, system: str, user: str, response_language: str) -> str:
        system_full = _apply_response_language(system, response_language, agent_id)
        if not self._client:
            raise RuntimeError(
                "LLM client unavailable: set OPENROUTER_API_KEY for OpenRouter."
            )
        extra_headers = _openrouter_extra_headers()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_full},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        r = self._client.chat.completions.create(**kwargs)
        raw = (r.choices[0].message.content or "").strip()
        self._append_log(agent_id, system_full, user, raw, response_language)
        return raw

    # --- Agents ----------------------------------------------------------

    def run_a14(self, block: dict, language_hint: str) -> str:
        roles = "\n".join(f"- {r}" for r in block["role_titles"])
        user = (
            f"Language: {language_hint}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            f"Corpus stages in this block: {len(block['phases'])} (one stage per phase_id).\n"
            f"Block title: {block['title']}\n"
            f"Audience: {block['audience']}\n"
            f"Roles:\n{roles}\n"
            f"Phases (with phase_id):\n{_phase_lines_for_prompt(block)}"
        )
        return self._complete("A14", prompts.SYSTEM_A14, user, language_hint)

    def run_a15(self, block: dict, language_hint: str) -> str:
        user = (
            f"Language: {language_hint}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            f"Corpus stages in this block: {len(block['phases'])} (one stage per phase_id).\n"
            f"Block title: {block['title']}\n"
            f"The session will use canonical questions from this block once scope is set.\n"
            f"Ask the user for their topic and background.\n"
            f"Phases (with phase_id):\n{_phase_lines_for_prompt(block)}"
        )
        return self._complete("A15", prompts.SYSTEM_A15, user, language_hint)

    def run_a16(
        self, block: dict, user_answer: str, messages: list[dict[str, str]], language_hint: str
    ) -> dict[str, Any]:
        user = (
            f"Response language: {language_hint}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Block: {block['title']}\n"
            f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            f"Corpus stage count: {len(block['phases'])} (equals number of phase_id entries).\n"
            f"Conversation:\n{_format_history(messages)}\n"
            f"Latest user answer:\n{user_answer}"
        )
        raw = self._complete("A16", prompts.SYSTEM_A16, user, language_hint)
        parsed = _extract_json_object(raw)
        if self._log_sink:
            self._log_sink[-1]["output_parsed"] = parsed
        return parsed

    def run_a13(
        self,
        messages: list[dict[str, str]],
        user_answer: str,
        language_hint: str,
        block: dict | None = None,
    ) -> str:
        ctx = ""
        if block is not None:
            ctx = (
                f"\nResearch block_id: {block['block_id']}\n"
                f"Block title: {block['title']}\n"
                f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            )
        user = (
            f"Language: {language_hint}\n"
            f"{ctx}"
            f"Conversation:\n{_format_history(messages)}\n"
            f"Unclear answer:\n{user_answer}"
        )
        return self._complete("A13", prompts.SYSTEM_A13, user, language_hint)

    def run_a17(
        self,
        low_score_reason: str,
        messages: list[dict[str, str]],
        language_hint: str,
        block: dict | None = None,
    ) -> str:
        ctx = ""
        if block is not None:
            ctx = (
                f"Research block_id: {block['block_id']}\n"
                f"Block title: {block['title']}\n"
                f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            )
        user = (
            f"Language: {language_hint}\n"
            f"{ctx}"
            f"low_score_reason: {low_score_reason}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        return self._complete("A17", prompts.SYSTEM_A17, user, language_hint)

    def run_a18(self, block: dict, a16_summary: dict[str, Any], messages: list[dict[str, str]], language_hint: str) -> str:
        ext = (a16_summary.get("extended_focus_area") or "").strip()
        ex = (a16_summary.get("extracted_focus_area") or "").strip()
        user = (
            f"Language: {language_hint}\n"
            f"main_topic (research block title): {block['title']}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Corpus interview has exactly {len(block['phases'])} thematic stage(s).\n"
            f"extracted_focus_area: {ex or '(none)'}\n"
            f"extended_focus_area: {ext or '(none)'}\n"
            f"Phases for the assistant message (copy titles only; use this numbering; NEVER show phase_id, "
            f"bracket labels, or internal IDs to the user):\n{_phase_human_numbered_titles(block)}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            "Write one message per system instructions: transition, bridge, same numbered list (titles only), "
            "then one substantive question about their expertise — NOT about confirming or changing the roadmap."
        )
        return self._complete("A18", prompts.SYSTEM_A18, user, language_hint)

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
            f"Research block_id: {block['block_id']}\n"
            f"Block: {block['title']}\n"
            f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            f"Phase titles (order): {phase_titles}\n"
            f"Conversation:\n{_format_history(messages)}\n"
            f"User reply to scope proposal:\n{user_answer}"
        )
        raw = self._complete("A19", prompts.SYSTEM_A19, user, language_hint)
        parsed = _extract_json_object(raw)
        if self._log_sink:
            self._log_sink[-1]["output_parsed"] = parsed
        return parsed

    def run_a20(
        self,
        suggested_modification: str,
        messages: list[dict[str, str]],
        language_hint: str,
        block: dict | None = None,
    ) -> str:
        ctx = ""
        if block is not None:
            ctx = (
                f"Research block_id: {block['block_id']}\n"
                f"Block title: {block['title']}\n"
                f"Phase map (phase_id → title): {_phase_map_for_prompt(block)}\n"
            )
        user = (
            f"Language: {language_hint}\n"
            f"{ctx}"
            f"suggested_modification: {suggested_modification}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        return self._complete("A20", prompts.SYSTEM_A20, user, language_hint)

    def run_a21(
        self,
        block: dict,
        phase_indices: list[int],
        language_hint: str,
    ) -> str:
        rows = []
        for i in phase_indices:
            ph = block["phases"][i]
            pid = ph.get("phase_id", i)
            rows.append(f"phase_id={pid!r} | {ph['title']}")
        rows_txt = "\n".join(rows)
        user = (
            f"Language: {language_hint}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Block title: {block['title']}\n"
            f"Selected corpus phases (in order):\n{rows_txt}\n"
            f"Write the short transition per system instructions. Do not list interview questions."
        )
        return self._complete("A21", prompts.SYSTEM_A21, user, language_hint)

    def localize_canonical_question(self, source_question: str, language_hint: str) -> str:
        lang = (language_hint or "English").strip() or "English"
        if lang.lower() == "english":
            return source_question.strip()
        user = (
            f"Mandatory response language: {lang}\n"
            f"Source language of the corpus question: English\n"
            f"Source question:\n{source_question.strip()}"
        )
        return self._complete("A22", prompts.SYSTEM_A22, user, language_hint)

    def run_synthesize_canonical_question(
        self,
        block: dict,
        phase_index: int,
        step_index: int,
        extracted_focus_area: str,
        extended_focus_area: str,
        messages: list[dict[str, str]],
        language_hint: str,
    ) -> str:
        ph = block["phases"][phase_index]
        sk = STEP_ORDER[step_index]
        roles = "\n".join(f"- {r}" for r in block["role_titles"])
        ext_short = (extracted_focus_area or "").strip()
        ext_long = (extended_focus_area or "").strip()
        specialty_digest = _user_messages_for_specialty_context(messages)
        user = (
            f"Language: {language_hint}\n"
            f"--- Anchor 1: research block ---\n"
            f"block_id (internal; never print to user): {block['block_id']}\n"
            f"Block title (macro domain / industry lane — use this vocabulary): {block['title']}\n"
            f"Audience line from corpus: {block['audience']}\n"
            f"Example role titles (indicative only):\n{roles}\n"
            f"--- Anchor 2: phase (sub-theme for this question) ---\n"
            f"Phase title: {ph['title']}\n"
            f"phase_id (internal; never print to user): {ph.get('phase_id')!r}\n"
            f"--- Anchor 3: respondent specialty (most important — ground the question here) ---\n"
            f"extracted_focus_area (A16 concise): {ext_short or '(none)'}\n"
            f"extended_focus_area (A16 fuller): {ext_long or '(none)'}\n"
            f"User messages only — chronological (specialty / role / experience in their own words):\n{specialty_digest}\n"
            f"--- Step type (defines the shape / depth of the question) ---\n"
            f"Step key: {sk}  (general = open exploration | deepening = process/mechanism/example | drilling = scenario/dilemma/stress-test)\n"
            f"--- Full dialogue tail (context) ---\n{_format_history(messages[-18:])}\n"
            f"Produce exactly one interview question in the mandatory language."
        )
        return self._complete("CANONICAL_Q", prompts.SYSTEM_CANONICAL_QUESTION, user, language_hint)

    def run_a11(self, block: dict, messages: list[dict[str, str]], language_hint: str) -> str:
        user = (
            f"Language: {language_hint}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Block title: {block['title']}\n"
            f"Phase map covered in this session (full block reference): {_phase_map_for_prompt(block)}\n"
            f"Conversation:\n{_format_history(messages)}"
        )
        return self._complete("A11", prompts.SYSTEM_A11, user, language_hint)

    def run_a22(self, block: dict, phase_index: int, language_hint: str) -> str:
        ph = block["phases"][phase_index]
        pid = ph.get("phase_id", phase_index)
        user = (
            f"Language: {language_hint}\n"
            f"Research block_id: {block['block_id']}\n"
            f"Next phase title (use this theme in natural language): {ph['title']}\n"
            f"Internal phase id (do NOT print this id or bracket labels to the user): {pid!r}"
        )
        return self._complete("A22", prompts.SYSTEM_A22, user, language_hint)

    def run_canonical_depth(
        self,
        canonical_question: str,
        user_answer: str,
        messages: list[dict[str, str]],
        language_hint: str,
        *,
        block: dict | None = None,
        phase_index: int | None = None,
        step_key: str | None = None,
    ) -> dict[str, Any]:
        corpus_ctx = ""
        if block is not None and phase_index is not None:
            ph = block["phases"][phase_index]
            pid = ph.get("phase_id", phase_index)
            corpus_ctx = (
                f"Research block_id: {block['block_id']}\n"
                f"Corpus phase_id: {pid!r}\n"
                f"Phase title: {ph['title']}\n"
                f"Corpus step key: {step_key or '?'}\n"
                f"(Within each phase_id, steps are general → deepening → drilling.)\n"
            )
        user = (
            f"Language: {language_hint}\n"
            f"{corpus_ctx}"
            f"Canonical question (fixed):\n{canonical_question}\n"
            f"Conversation (excerpt):\n{_format_history(messages[-12:])}\n"
            f"User answer:\n{user_answer}"
        )
        raw = self._complete("CANONICAL_DEPTH", prompts.SYSTEM_CANONICAL_DEPTH, user, language_hint)
        parsed = _extract_json_object(raw)
        if self._log_sink:
            self._log_sink[-1]["output_parsed"] = parsed
        return parsed
