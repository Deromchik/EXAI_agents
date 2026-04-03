from state_machine import advance_canonical_position, decide_after_a16


def test_advance_subset_phases():
    block = {
        "phases": [
            {"title": "P0"},
            {"title": "P1"},
            {"title": "P2"},
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
