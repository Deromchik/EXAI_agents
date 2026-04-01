"""
Flow helpers for scoping (diagram Step 1–3) and canonical interview steps.
Pure functions — no Streamlit / network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

DEFAULT_SCORE_THRESHOLD = 0.5
UNDERSTANDING_FLOOR = 0.7

STEP_ORDER = ("general", "deepening", "drilling")


class ScopingWait(str, Enum):
    """Where we wait for the user's next chat message during scoping."""

    OPENING = "opening"
    AFTER_A13_OPENING = "after_a13_opening"
    AFTER_A17_OPENING = "after_a17_opening"
    SCOPE = "scope"
    AFTER_A13_SCOPE = "after_a13_scope"
    AFTER_A20_SCOPE = "after_a20_scope"


class MainPhase(str, Enum):
    SETUP = "setup"
    SCOPING = "scoping"
    CANONICAL = "canonical"
    DONE = "done"


@dataclass
class FlowState:
    main_phase: MainPhase = MainPhase.SETUP
    diagram_step: int = 1
    scoping_wait: ScopingWait | None = None
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    selected_phase_indices: list[int] = field(default_factory=list)
    canonical_phase_index: int = 0
    canonical_phase_slot: int = 0
    canonical_step_index: int = 0
    canonical_reask_used: bool = False
    last_a16: dict[str, Any] | None = None
    last_a19: dict[str, Any] | None = None


def phase_count(block: dict) -> int:
    return len(block["phases"])


def get_canonical_step_key(step_index: int) -> str:
    return STEP_ORDER[step_index]


def canonical_step_prompt(block: dict, phase_index: int, step_index: int) -> str:
    """Returns the step key (general / deepening / drilling) as a plain string.
    Questions are synthesized dynamically by CANONICAL_Q — no static text in JSON.
    """
    return get_canonical_step_key(step_index)


def canonical_question_text(block: dict, phase_index: int, step_index: int) -> str:
    """Alias for `canonical_step_prompt`."""
    return canonical_step_prompt(block, phase_index, step_index)


def all_phase_indices(block: dict) -> list[int]:
    return list(range(phase_count(block)))


def decide_after_a16(
    result: dict[str, Any],
    threshold: float,
    understanding_floor: float = UNDERSTANDING_FLOOR,
) -> str:
    """
    Returns: 'a13' | 'a17' | 'advance_step2'
    Mirrors `system_prompt_builder` Stage-1 branching: incomprehensible → A13;
    `should_agent_reask` from A16 (like a_16_prompt) forces A17 before advancing.
    """
    au = float(result.get("answer_understanding_score") or 0)
    if au < understanding_floor:
        return "a13"
    try:
        sar = int(result.get("should_agent_reask", 0))
    except (TypeError, ValueError):
        sar = 0
    if sar == 1:
        return "a17"
    pu = float(result.get("purpose_understanding_score") or 0)
    fs = float(result.get("focus_specificity_score") or 0)
    if pu > threshold and fs > threshold:
        return "advance_step2"
    return "a17"


def decide_after_a19(
    result: dict[str, Any],
    threshold: float,
    understanding_floor: float = UNDERSTANDING_FLOOR,
) -> str:
    """
    Returns: 'a13' | 'a20' | 'advance_step3'
    `should_agent_reask` (cf. a_19_prompt) forces A20 when the reply needs narrowing
    even if other fields look acceptable.
    """
    au = float(result.get("answer_understanding_score") or 0)
    if au < understanding_floor:
        return "a13"
    try:
        sar = int(result.get("should_agent_reask", 0))
    except (TypeError, ValueError):
        sar = 0
    if sar == 1:
        return "a20"
    sa = float(result.get("scope_agreement_score") or 0)
    if sa > threshold:
        return "advance_step3"
    return "a20"


def advance_canonical_position(
    block: dict,
    selected_phase_indices: list[int],
    phase_slot: int,
    step_index: int,
) -> tuple[str, int | None, int | None, int | None]:
    """
    After finishing one canonical step within selected phases, return
    (action, new_phase_slot, new_phase_index, new_step_idx).
    action: 'next_step' | 'next_phase' | 'finished_all'
    """
    if not selected_phase_indices:
        return "finished_all", None, None, None
    phase_index = selected_phase_indices[phase_slot]
    if step_index + 1 < len(STEP_ORDER):
        return "next_step", phase_slot, phase_index, step_index + 1
    if phase_slot + 1 < len(selected_phase_indices):
        next_pi = selected_phase_indices[phase_slot + 1]
        return "next_phase", phase_slot + 1, next_pi, 0
    return "finished_all", None, None, None


def resolve_phase_indices_from_scope_areas(block: dict, scope_areas: list[str] | None) -> list[int]:
    """Map A19 scope_areas strings to phase indices; default all phases if empty or no match."""
    titles = [ph["title"] for ph in block["phases"]]
    n = len(titles)
    if not scope_areas:
        return list(range(n))
    idx: list[int] = []
    for a in scope_areas:
        a_l = (a or "").strip().lower()
        if not a_l:
            continue
        for i, t in enumerate(titles):
            t_l = t.lower()
            if a_l in t_l or t_l in a_l or a_l[:20] in t_l:
                if i not in idx:
                    idx.append(i)
    return idx if idx else list(range(n))


def collect_planned_questions_for_phases(block: dict, phase_indices: list[int]) -> list[tuple[int, str, str]]:
    """List of (phase_index, phase_title, step_key) in order.
    Questions are synthesized dynamically — step_key is 'general' / 'deepening' / 'drilling'.
    """
    out: list[tuple[int, str, str]] = []
    for pi in phase_indices:
        ph = block["phases"][pi]
        title = ph["title"]
        for sk in STEP_ORDER:
            out.append((pi, title, sk))
    return out
