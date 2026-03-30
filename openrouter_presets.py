"""
Recommended OpenRouter models for interview flows and JSON-style outputs (A16, A19, depth).
Model IDs match the catalog at https://openrouter.ai/models
"""

from __future__ import annotations

import os

# (model_id, short UI label)
RECOMMENDED_OPENROUTER_MODELS: list[tuple[str, str]] = [
    (
        "openai/gpt-4o-mini",
        "GPT-4o mini — fast, cost-effective, reliable JSON",
    ),
    (
        "openai/gpt-4o",
        "GPT-4o — higher quality dialogue and instruction following",
    ),
    (
        "anthropic/claude-3.5-sonnet",
        "Claude 3.5 Sonnet — strong dialogue and nuance",
    ),
    (
        "anthropic/claude-3-haiku",
        "Claude 3 Haiku — very fast responses",
    ),
    (
        "google/gemini-2.0-flash-001",
        "Gemini 2.0 Flash — fast, strong multilingual support",
    ),
    (
        "meta-llama/llama-3.3-70b-instruct",
        "Llama 3.3 70B — open weights, solid balance",
    ),
    (
        "mistralai/mistral-small-3.1-24b-instruct-2503",
        "Mistral Small 3.1 24B — lightweight and economical",
    ),
]


def default_model_id() -> str:
    env = os.getenv("OPENROUTER_MODEL", "").strip()
    if env:
        return env
    return RECOMMENDED_OPENROUTER_MODELS[0][0]


def preset_index_for_id(model_id: str) -> int:
    for i, (mid, _) in enumerate(RECOMMENDED_OPENROUTER_MODELS):
        if mid == model_id:
            return i
    return 0
