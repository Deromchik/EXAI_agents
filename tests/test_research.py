import pytest

from content.research_loader import (
    ResearchValidationError,
    get_block_by_id,
    load_research_blocks,
    validate_research_blocks,
)


def test_validate_research_blocks_ok():
    validate_research_blocks()


def test_all_block_ids_present():
    doc = load_research_blocks()
    ids = {b["block_id"] for b in doc["blocks"]}
    assert ids == {1, 2, 3, 4, 5, 6, 7}


def test_block3_single_phase():
    b = get_block_by_id(3)
    assert b is not None
    assert len(b["phases"]) == 1


def test_validate_rejects_missing_phase_title():
    bad = {
        "blocks": [
            {
                "block_id": 99,
                "title": "T",
                "audience": "A",
                "role_titles": ["R"],
                "phases": [
                    {
                        "phase_id": "x",
                        "title": "",
                    }
                ],
            }
        ]
    }
    with pytest.raises(ResearchValidationError):
        validate_research_blocks(bad)


def test_validate_rejects_missing_phase_id():
    bad = {
        "blocks": [
            {
                "block_id": 98,
                "title": "T",
                "audience": "A",
                "role_titles": ["R"],
                "phases": [
                    {
                        "phase_id": "",
                        "title": "Some phase",
                    }
                ],
            }
        ]
    }
    with pytest.raises(ResearchValidationError):
        validate_research_blocks(bad)
