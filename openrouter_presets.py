"""
OpenRouter models allowed in the Streamlit UI (whitelist only).
Model IDs match the catalog at https://openrouter.ai/models
"""

from __future__ import annotations

import os

# Default when OPENROUTER_MODEL is unset or not in RECOMMENDED_OPENROUTER_MODELS
DEFAULT_OPENROUTER_MODEL_ID = "google/gemini-3.1-flash-lite-preview"

# (model_id, short UI label) — order shown in Streamlit
RECOMMENDED_OPENROUTER_MODELS: list[tuple[str, str]] = [
    ("openai/gpt-4o-mini", "GPT-4o mini"),
    ("openai/gpt-5.4-nano", "GPT-5.4 nano"),
    ("openai/gpt-5.4-mini", "GPT-5.4 mini"),
    ("mistralai/mistral-small-2603", "Mistral Small 2603"),
    ("google/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite (preview, default)"),
]

_ALLOWED_IDS = frozenset(mid for mid, _ in RECOMMENDED_OPENROUTER_MODELS)


def default_model_id() -> str:
    env = os.getenv("OPENROUTER_MODEL", "").strip()
    if env in _ALLOWED_IDS:
        return env
    return DEFAULT_OPENROUTER_MODEL_ID


def preset_index_for_id(model_id: str) -> int:
    for i, (mid, _) in enumerate(RECOMMENDED_OPENROUTER_MODELS):
        if mid == model_id:
            return i
    return next(
        i
        for i, (mid, _) in enumerate(RECOMMENDED_OPENROUTER_MODELS)
        if mid == DEFAULT_OPENROUTER_MODEL_ID
    )
