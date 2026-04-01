from state_machine import (
    advance_canonical_position,
    decide_after_a16,
    decide_after_a19,
    resolve_phase_indices_from_scope_areas,
)


def test_advance_subset_phases():
    block = {
        "phases": [
            {"title": "P0", "steps": {"general": "a", "deepening": "b", "drilling": "c"}},
            {"title": "P1", "steps": {"general": "d", "deepening": "e", "drilling": "f"}},
            {"title": "P2", "steps": {"general": "g", "deepening": "h", "drilling": "i"}},
        ]
    }
    sel = [0, 2]
    assert advance_canonical_position(block, sel, 0, 0)[0] == "next_step"
    assert advance_canonical_position(block, sel, 0, 2)[0] == "next_phase"
    assert advance_canonical_position(block, sel, 1, 2)[0] == "finished_all"


def test_decide_after_a16_should_agent_reask_forces_a17():
    r = {
        "answer_understanding_score": 1.0,
        "should_agent_reask": 1,
        "purpose_understanding_score": 1.0,
        "focus_specificity_score": 1.0,
    }
    assert decide_after_a16(r, 0.5) == "a17"


def test_decide_after_a19_should_agent_reask_forces_a20():
    r = {
        "answer_understanding_score": 1.0,
        "should_agent_reask": 1,
        "scope_agreement_score": 1.0,
    }
    assert decide_after_a19(r, 0.5) == "a20"


def test_resolve_scope_areas():
    block = {
        "phases": [
            {"title": "Alpha phase", "steps": {"general": "x", "deepening": "y", "drilling": "z"}},
            {"title": "Beta", "steps": {"general": "x", "deepening": "y", "drilling": "z"}},
        ]
    }
    assert resolve_phase_indices_from_scope_areas(block, None) == [0, 1]
    assert resolve_phase_indices_from_scope_areas(block, ["Beta"]) == [1]
