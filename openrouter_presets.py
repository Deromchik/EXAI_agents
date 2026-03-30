"""
Рекомендовані моделі OpenRouter для інтерв’ю + JSON-аналізу (A16, A19, depth).
Ідентифікатори — як у каталозі https://openrouter.ai/models
"""

from __future__ import annotations

import os

# (model_id, короткий підпис для UI)
RECOMMENDED_OPENROUTER_MODELS: list[tuple[str, str]] = [
    (
        "openai/gpt-4o-mini",
        "GPT-4o mini — швидко, економно, стабільний JSON",
    ),
    (
        "openai/gpt-4o",
        "GPT-4o — вища якість діалогу та інструкцій",
    ),
    (
        "anthropic/claude-3.5-sonnet",
        "Claude 3.5 Sonnet — сильний діалог і нюанси",
    ),
    (
        "anthropic/claude-3-haiku",
        "Claude 3 Haiku — дуже швидкі відповіді",
    ),
    (
        "google/gemini-2.0-flash-001",
        "Gemini 2.0 Flash — швидко, добре для багатомовності",
    ),
    (
        "meta-llama/llama-3.3-70b-instruct",
        "Llama 3.3 70B — відкрита модель, добрий баланс",
    ),
    (
        "mistralai/mistral-small-3.1-24b-instruct-2503",
        "Mistral Small 3.1 24B — легка та дешева",
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
