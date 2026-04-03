"""Load and validate canonical research content from JSON.

Questions for the three step types (general / deepening / drilling) are synthesized
dynamically by the CANONICAL_Q agent from block title, phase title, and the
respondent's stated specialty — there are no static question texts in the JSON.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent
_DEFAULT_JSON = _CONTENT_DIR / "research_blocks.json"

STEP_KEYS = ("general", "deepening", "drilling")


class ResearchValidationError(ValueError):
    pass


def load_research_blocks(path: Path | None = None) -> dict:
    p = path or _DEFAULT_JSON
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def validate_research_blocks(data: dict | None = None, path: Path | None = None) -> None:
    """Assert structure: blocks → phases (each with non-empty phase_id and title).
    No 'steps' field is required; questions are generated dynamically.
    """
    raw = data if data is not None else load_research_blocks(path)
    blocks = raw.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ResearchValidationError("'blocks' must be a non-empty list")

    seen_ids: set[int] = set()
    for b in blocks:
        bid = b.get("block_id")
        if not isinstance(bid, int):
            raise ResearchValidationError(f"block_id must be int, got {bid!r}")
        if bid in seen_ids:
            raise ResearchValidationError(f"duplicate block_id {bid}")
        seen_ids.add(bid)

        for key in ("title", "audience"):
            if not (isinstance(b.get(key), str) and b[key].strip()):
                raise ResearchValidationError(f"block {bid}: missing {key}")

        roles = b.get("role_titles")
        if not isinstance(roles, list) or not roles:
            raise ResearchValidationError(f"block {bid}: role_titles must be non-empty list")

        phases = b.get("phases")
        if not isinstance(phases, list) or not phases:
            raise ResearchValidationError(f"block {bid}: phases must be non-empty list")

        for ph in phases:
            if not (isinstance(ph.get("title"), str) and ph["title"].strip()):
                raise ResearchValidationError(f"block {bid}: phase needs title")
            pid = ph.get("phase_id")
            if not (isinstance(pid, str) and pid.strip()):
                raise ResearchValidationError(
                    f"block {bid}: each phase must have non-empty string phase_id"
                )


@lru_cache(maxsize=1)
def get_validated_document() -> dict:
    doc = load_research_blocks()
    validate_research_blocks(doc)
    return doc


def get_block_by_id(block_id: int) -> dict | None:
    doc = get_validated_document()
    for b in doc["blocks"]:
        if b["block_id"] == block_id:
            return b
    return None


def list_blocks_summary() -> list[tuple[int, str, str]]:
    """(block_id, title, audience) for UI."""
    doc = get_validated_document()
    return [(b["block_id"], b["title"], b["audience"]) for b in doc["blocks"]]


# --- Session duration (shown in UI and opening prompts) -------------------

DEFAULT_SESSION_MINUTES = 90  # same for every research block


def estimate_session_minutes(block: dict) -> int:
    """Return default interview duration in minutes (fixed for all blocks)."""
    return DEFAULT_SESSION_MINUTES


def estimate_session_label(block: dict) -> str:
    """Human-readable duration string, e.g. '~90 minutes'."""
    return f"~{estimate_session_minutes(block)} minutes"
