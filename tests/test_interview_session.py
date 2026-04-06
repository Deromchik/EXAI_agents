"""Session export/import helpers."""

from interview_session import (
    SESSION_EXPORT_FORMAT,
    apply_block_to_import_result,
    build_session_export_dict,
    parse_session_object,
    session_import_result_to_state_updates,
)
from state_machine import FlowState, MainPhase, ScopingWait

_BLOCK = {
    "block_id": 99,
    "title": "Test block",
    "phases": [
        {"phase_id": "99-1", "title": "Alpha"},
        {"phase_id": "99-2", "title": "Beta"},
    ],
}


def test_session_export_import_roundtrip_canonical():
    flow = FlowState(
        main_phase=MainPhase.CANONICAL,
        diagram_step=3,
        scoping_wait=None,
        score_threshold=0.55,
        selected_phase_indices=[0, 1],
        canonical_phase_index=1,
        canonical_phase_slot=1,
        canonical_step_index=2,
        last_a16={"extracted_focus_area": "x", "extended_focus_area": "y"},
    )
    messages = [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "Hello"}]
    export = build_session_export_dict(
        messages=messages,
        block_id=99,
        flow=flow,
        opening_generated=True,
        awaiting_canonical_reask=False,
        current_canonical_question_plain="Q?",
        response_language="English",
        openrouter_model_id="openai/gpt-4o",
        llm_temperature=0.1,
        block=_BLOCK,
    )
    assert export["format"] == SESSION_EXPORT_FORMAT
    assert export["block_id"] == 99
    assert export["selected_phase_ids"] == ["99-1", "99-2"]
    pos = export["position"]
    assert pos["phase_id"] == "99-2"
    assert pos["step_key"] == "drilling"
    assert pos["pending_turn"] == "canonical_question_answer"

    result = parse_session_object(export)
    assert not result.legacy_messages_only
    assert result.messages == messages
    w = apply_block_to_import_result(result, _BLOCK, export)
    assert w == []
    assert result.flow.canonical_phase_index == 1
    assert result.flow.selected_phase_indices == [0, 1]

    updates = session_import_result_to_state_updates(result)
    assert updates["block_id"] == 99
    assert updates["flow"].main_phase == MainPhase.CANONICAL


def test_legacy_array_only():
    result = parse_session_object([{"role": "user", "content": "a"}])
    assert result.legacy_messages_only
    assert result.interview_started is False


def test_realign_phase_id_after_corpus_shift():
    """If phase_index no longer matches phase_id, remap by position.phase_id."""
    export = {
        "format": SESSION_EXPORT_FORMAT,
        "block_id": 99,
        "messages": [],
        "flow": {
            "main_phase": "canonical",
            "diagram_step": 3,
            "scoping_wait": None,
            "score_threshold": 0.5,
            "selected_phase_indices": [0, 1],
            "canonical_phase_index": 0,
            "canonical_phase_slot": 0,
            "canonical_step_index": 0,
            "last_a16": None,
        },
        "opening_generated": True,
        "awaiting_canonical_reask": False,
        "current_canonical_question_plain": "",
        "response_language": "English",
        "openrouter_model_id": None,
        "llm_temperature": 0.1,
        "position": {
            "main_phase": "canonical",
            "phase_id": "99-2",
            "phase_title": "Beta",
        },
        "selected_phase_ids": ["99-1", "99-2"],
    }
    result = parse_session_object(export)
    w = apply_block_to_import_result(result, _BLOCK, export)
    assert any("realigned" in x for x in w)
    assert result.flow.canonical_phase_index == 1
