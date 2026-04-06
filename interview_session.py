"""
Full interview session export/import (JSON) — same shape for download and upload.

Legacy: a bare JSON list of {role, content} (old app downloads) loads messages only
and cannot restore pipeline position.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from state_machine import (
    FlowState,
    MainPhase,
    ScopingWait,
    get_canonical_step_key,
    phase_count,
)

SESSION_EXPORT_FORMAT = "exai_interview_session_v1"


def _flow_to_dict(flow: FlowState) -> dict[str, Any]:
    return {
        "main_phase": flow.main_phase.value,
        "diagram_step": flow.diagram_step,
        "scoping_wait": flow.scoping_wait.value if flow.scoping_wait else None,
        "score_threshold": flow.score_threshold,
        "selected_phase_indices": list(flow.selected_phase_indices),
        "canonical_phase_index": flow.canonical_phase_index,
        "canonical_phase_slot": flow.canonical_phase_slot,
        "canonical_step_index": flow.canonical_step_index,
        "canonical_follow_ups_used": flow.canonical_follow_ups_used,
        "last_a16": flow.last_a16,
    }


def _flow_from_dict(d: dict[str, Any]) -> FlowState:
    sw = d.get("scoping_wait")
    return FlowState(
        main_phase=MainPhase(d["main_phase"]),
        diagram_step=int(d.get("diagram_step", 1)),
        scoping_wait=ScopingWait(sw) if sw else None,
        score_threshold=float(d.get("score_threshold", 0.5)),
        selected_phase_indices=[int(x) for x in d.get("selected_phase_indices") or []],
        canonical_phase_index=int(d.get("canonical_phase_index", 0)),
        canonical_phase_slot=int(d.get("canonical_phase_slot", 0)),
        canonical_step_index=int(d.get("canonical_step_index", 0)),
        canonical_follow_ups_used=int(d.get("canonical_follow_ups_used", 0)),
        last_a16=d.get("last_a16"),
    )


def _selected_phase_ids_from_indices(block: dict, indices: list[int]) -> list[str]:
    phases = block["phases"]
    out: list[str] = []
    for i in indices:
        if 0 <= i < len(phases):
            out.append(str(phases[i]["phase_id"]))
    return out


def _indices_from_phase_ids(block: dict, ids: list[str]) -> list[int] | None:
    if not ids:
        return []
    by_id = {str(p["phase_id"]): i for i, p in enumerate(block["phases"])}
    out: list[int] = []
    for pid in ids:
        if pid not in by_id:
            return None
        out.append(by_id[pid])
    return out


def build_position_snapshot(
    block: dict | None,
    flow: FlowState,
    *,
    awaiting_canonical_reask: bool = False,
    opening_generated: bool = False,
) -> dict[str, Any]:
    """Human- and machine-readable markers for the current place in the interview."""
    out: dict[str, Any] = {
        "main_phase": flow.main_phase.value,
        "diagram_step": flow.diagram_step,
        "scoping_wait": flow.scoping_wait.value if flow.scoping_wait else None,
        "opening_generated": opening_generated,
        "awaiting_canonical_reask": awaiting_canonical_reask,
    }
    if flow.main_phase == MainPhase.CANONICAL:
        out["pending_turn"] = (
            "canonical_reask_follow_up" if awaiting_canonical_reask else "canonical_question_answer"
        )
    elif flow.main_phase == MainPhase.SCOPING:
        out["pending_turn"] = "scoping_user_reply"
    elif flow.main_phase == MainPhase.DONE:
        out["pending_turn"] = "session_complete"
    if not block:
        return out
    bid = block.get("block_id")
    if bid is not None:
        out["block_id"] = bid
    out["block_title"] = str(block.get("title") or "")

    if flow.main_phase == MainPhase.CANONICAL and flow.selected_phase_indices:
        pi = flow.canonical_phase_index
        phases = block["phases"]
        if 0 <= pi < len(phases):
            ph = phases[pi]
            out["phase_id"] = ph["phase_id"]
            out["phase_title"] = ph["title"]
            out["phase_index"] = pi
            out["step_index"] = flow.canonical_step_index
            out["step_key"] = get_canonical_step_key(flow.canonical_step_index)
        out["canonical_phase_slot"] = flow.canonical_phase_slot
        out["selected_phase_ids"] = _selected_phase_ids_from_indices(
            block, flow.selected_phase_indices
        )
    return out


def build_session_export_dict(
    *,
    messages: list[dict[str, Any]],
    block_id: int | None,
    flow: FlowState,
    opening_generated: bool,
    awaiting_canonical_reask: bool,
    current_canonical_question_plain: str,
    response_language: str,
    openrouter_model_id: str | None,
    llm_temperature: float,
    block: dict | None,
) -> dict[str, Any]:
    flow_d = _flow_to_dict(flow)
    selected_ids = (
        _selected_phase_ids_from_indices(block, flow.selected_phase_indices)
        if block and flow.selected_phase_indices
        else []
    )
    payload: dict[str, Any] = {
        "format": SESSION_EXPORT_FORMAT,
        "block_id": block_id,
        "response_language": response_language,
        "openrouter_model_id": openrouter_model_id,
        "llm_temperature": llm_temperature,
        "opening_generated": opening_generated,
        "awaiting_canonical_reask": awaiting_canonical_reask,
        "current_canonical_question_plain": current_canonical_question_plain,
        "messages": messages,
        "flow": flow_d,
        "position": build_position_snapshot(
            block,
            flow,
            awaiting_canonical_reask=awaiting_canonical_reask,
            opening_generated=opening_generated,
        ),
    }
    if selected_ids:
        payload["selected_phase_ids"] = selected_ids
    return payload


@dataclass
class SessionImportResult:
    """Normalized session fields to merge into Streamlit session_state."""

    messages: list[dict[str, Any]]
    block_id: int
    flow: FlowState
    opening_generated: bool
    awaiting_canonical_reask: bool
    current_canonical_question_plain: str
    response_language: str | None
    openrouter_model_id: str | None
    llm_temperature: float | None
    score_threshold: float | None
    interview_started: bool
    legacy_messages_only: bool
    warnings: list[str]


def _validate_flow_against_block(block: dict, flow: FlowState) -> list[str]:
    warnings: list[str] = []
    n = phase_count(block)
    if flow.main_phase == MainPhase.CANONICAL:
        if flow.selected_phase_indices:
            for i in flow.selected_phase_indices:
                if i < 0 or i >= n:
                    warnings.append(f"Invalid phase index {i} in selected_phase_indices; clamping.")
            flow.selected_phase_indices = [i for i in flow.selected_phase_indices if 0 <= i < n]
        if not flow.selected_phase_indices:
            flow.selected_phase_indices = list(range(n))
            warnings.append("Rebuilt selected_phase_indices to all phases (was empty).")
        if flow.canonical_phase_slot < 0 or flow.canonical_phase_slot >= len(flow.selected_phase_indices):
            flow.canonical_phase_slot = 0
            warnings.append("Reset canonical_phase_slot to 0 (out of range).")
        pi = flow.canonical_phase_index
        if pi < 0 or pi >= n:
            flow.canonical_phase_index = flow.selected_phase_indices[0]
            warnings.append("Adjusted canonical_phase_index (was out of range).")
        if flow.canonical_step_index < 0 or flow.canonical_step_index > 2:
            flow.canonical_step_index = min(max(flow.canonical_step_index, 0), 2)
            warnings.append("Clamped canonical_step_index to 0..2.")
        if flow.canonical_phase_index not in flow.selected_phase_indices:
            flow.canonical_phase_index = flow.selected_phase_indices[0]
            flow.canonical_phase_slot = 0
            warnings.append("canonical_phase_index was not in selected_phase_indices; reset to first selected.")
        else:
            flow.canonical_phase_slot = flow.selected_phase_indices.index(flow.canonical_phase_index)
    return warnings


def parse_session_json_bytes(raw: bytes) -> SessionImportResult:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    return parse_session_object(obj)


def parse_session_object(obj: Any) -> SessionImportResult:
    warnings: list[str] = []

    if isinstance(obj, list):
        msgs = _normalize_messages(obj)
        return SessionImportResult(
            messages=msgs,
            block_id=0,
            flow=FlowState(main_phase=MainPhase.SETUP),
            opening_generated=False,
            awaiting_canonical_reask=False,
            current_canonical_question_plain="",
            response_language=None,
            openrouter_model_id=None,
            llm_temperature=None,
            score_threshold=None,
            interview_started=False,
            legacy_messages_only=True,
            warnings=[
                "Legacy format: JSON array of messages only — full session resume is not possible. "
                "Use a file exported as "
                + SESSION_EXPORT_FORMAT
                + " from this app version."
            ],
        )

    if not isinstance(obj, dict):
        raise ValueError("Root JSON value must be an object or a list of messages.")

    fmt = obj.get("format")
    if fmt != SESSION_EXPORT_FORMAT:
        raise ValueError(
            f"Unsupported export format {fmt!r}; expected {SESSION_EXPORT_FORMAT!r} for full resume."
        )

    messages = _normalize_messages(obj.get("messages"))
    block_id = obj.get("block_id")
    if not isinstance(block_id, int):
        raise ValueError("Missing or invalid integer block_id.")

    flow = _flow_from_dict(obj.get("flow") or {})
    opening_generated = bool(obj.get("opening_generated", False))
    awaiting = bool(obj.get("awaiting_canonical_reask", False))
    qplain = str(obj.get("current_canonical_question_plain") or "")
    rl = obj.get("response_language")
    response_language = str(rl) if isinstance(rl, str) and rl.strip() else None
    mid = obj.get("openrouter_model_id")
    openrouter_model_id = str(mid).strip() if mid else None
    try:
        llm_temperature = float(obj["llm_temperature"])
    except (KeyError, TypeError, ValueError):
        llm_temperature = None
    sth = flow.score_threshold
    score_threshold = float(sth) if sth is not None else None

    return SessionImportResult(
        messages=messages,
        block_id=block_id,
        flow=flow,
        opening_generated=opening_generated,
        awaiting_canonical_reask=awaiting,
        current_canonical_question_plain=qplain,
        response_language=response_language,
        openrouter_model_id=openrouter_model_id,
        llm_temperature=llm_temperature,
        score_threshold=score_threshold,
        interview_started=True,
        legacy_messages_only=False,
        warnings=warnings,
    )


def apply_block_to_import_result(
    result: SessionImportResult, block: dict, export_root: dict[str, Any]
) -> list[str]:
    """Validate and fix flow using corpus block; returns extra warnings."""
    if result.legacy_messages_only:
        return []
    warnings: list[str] = []
    flow = result.flow
    ids = export_root.get("selected_phase_ids")
    if isinstance(ids, list) and ids:
        mapped = _indices_from_phase_ids(block, [str(x) for x in ids])
        if mapped is not None:
            flow.selected_phase_indices = mapped
        else:
            warnings.append(
                "selected_phase_ids from file did not match current research_blocks.json; "
                "using indices from flow."
            )

    pos = export_root.get("position")
    if isinstance(pos, dict) and flow.main_phase == MainPhase.CANONICAL:
        pid = pos.get("phase_id")
        if pid and isinstance(pid, str):
            pi_stored = flow.canonical_phase_index
            phases = block["phases"]
            match = 0 <= pi_stored < len(phases) and phases[pi_stored]["phase_id"] == pid
            if not match:
                for i, p in enumerate(phases):
                    if p["phase_id"] == pid:
                        flow.canonical_phase_index = i
                        warnings.append(
                            "canonical_phase_index realigned using position.phase_id "
                            "because corpus or indices drifted."
                        )
                        break

    warnings.extend(_validate_flow_against_block(block, flow))
    return warnings


def _normalize_messages(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "")
        if role in ("user", "assistant", "system"):
            out.append({"role": role, "content": content})
    return out


def session_import_result_to_state_updates(result: SessionImportResult) -> dict[str, Any]:
    """Map import result to st.session_state keys (caller merges)."""
    updates: dict[str, Any] = {
        "interview_started": result.interview_started,
        "messages": result.messages,
        "block_id": result.block_id,
        "flow": result.flow,
        "opening_generated": result.opening_generated,
        "awaiting_canonical_reask": result.awaiting_canonical_reask,
        "current_canonical_question_plain": result.current_canonical_question_plain,
        "agent_logs": [],
    }
    if result.response_language is not None:
        updates["response_language"] = result.response_language
    if result.openrouter_model_id:
        updates["openrouter_model_id"] = result.openrouter_model_id
    if result.llm_temperature is not None:
        updates["llm_temperature"] = result.llm_temperature
    if result.score_threshold is not None:
        updates["score_threshold"] = result.score_threshold
    return updates
