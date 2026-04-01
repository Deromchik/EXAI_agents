"""Load and validate canonical research content from JSON."""

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
    """Assert structure: blocks with phases; each phase has three non-empty step instruction strings (general/deepening/drilling briefs)."""
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
            steps = ph.get("steps")
            if not isinstance(steps, dict):
                raise ResearchValidationError(f"block {bid}: phase steps must be dict")
            for sk in STEP_KEYS:
                t = steps.get(sk)
                if not isinstance(t, str) or not t.strip():
                    raise ResearchValidationError(
                        f"block {bid} phase {ph.get('phase_id')!r}: step {sk!r} empty or missing"
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
