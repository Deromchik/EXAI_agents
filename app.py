"""
Streamlit app: block selection, scoping agents A11–A22, canonical questions from JSON.
"""

from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv

from agents.runner import AgentRunner
from openrouter_presets import RECOMMENDED_OPENROUTER_MODELS, default_model_id
from content.research_loader import (
    get_block_by_id,
    list_blocks_summary,
    validate_research_blocks,
)
from state_machine import (
    FlowState,
    MainPhase,
    ScopingWait,
    advance_canonical_position,
    all_phase_indices,
    canonical_question_text,
    decide_after_a16,
    decide_after_a19,
    get_canonical_step_key,
    resolve_phase_indices_from_scope_areas,
)
from state_machine import DEFAULT_SCORE_THRESHOLD

load_dotenv()


def _apply_streamlit_secrets_to_env() -> None:
    """Streamlit Community Cloud: copy st.secrets into os.environ for OpenAI client."""
    try:
        sec = st.secrets
    except (RuntimeError, AttributeError):
        return
    if sec is None:
        return
    for key in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_HTTP_REFERER",
        "OPENROUTER_X_TITLE",
        "OPENAI_MODEL",
        "EXAI_MOCK_LLM",
    ):
        if key in sec:
            val = sec[key]
            if val is not None and str(val).strip():
                os.environ[key] = str(val).strip()


# Fail fast if corpus is invalid
validate_research_blocks()

STEP_LABELS = {
    "general": "Шаг 1 (Общее)",
    "deepening": "Шаг 2 (Углубление)",
    "drilling": "Шаг 3 (Drilling)",
}


def _runner() -> AgentRunner:
    mock = st.session_state.get("mock_llm", os.getenv("EXAI_MOCK_LLM", "0").lower() in ("1", "true", "yes"))
    model = st.session_state.get("openrouter_model_id")
    return AgentRunner(mock=mock, model=model)


def _lang_hint() -> str:
    return st.session_state.get("ui_language", "Ukrainian")


def _reset_interview(block_id: int, use_preset: bool) -> None:
    st.session_state.flow = FlowState(
        main_phase=MainPhase.SCOPING,
        diagram_step=1,
        scoping_wait=None,
        score_threshold=st.session_state.get("score_threshold", DEFAULT_SCORE_THRESHOLD),
        selected_phase_indices=[],
        canonical_phase_index=0,
        canonical_phase_slot=0,
        canonical_step_index=0,
        canonical_reask_used=False,
        last_a16=None,
        last_a19=None,
    )
    st.session_state.messages = []
    st.session_state.block_id = block_id
    st.session_state.use_preset = use_preset
    st.session_state.opening_generated = False
    st.session_state.post_a21_shown = False
    st.session_state.awaiting_canonical_reask = False


def _append_assistant(text: str) -> None:
    st.session_state.messages.append({"role": "assistant", "content": text})


def _append_user(text: str) -> None:
    st.session_state.messages.append({"role": "user", "content": text})


def _ensure_opening() -> None:
    if st.session_state.get("opening_generated"):
        return
    block = get_block_by_id(st.session_state.block_id)
    if not block:
        return
    runner = _runner()
    lang = _lang_hint()
    if st.session_state.use_preset:
        text = runner.run_a14(block, lang)
    else:
        text = runner.run_a15(lang)
    _append_assistant(text)
    st.session_state.opening_generated = True
    st.session_state.flow.scoping_wait = ScopingWait.OPENING


def _handle_scoping_user_message(user_text: str) -> None:
    block = get_block_by_id(st.session_state.block_id)
    if not block:
        return
    flow = st.session_state.flow
    th = flow.score_threshold
    runner = _runner()
    lang = _lang_hint()
    wait = flow.scoping_wait
    msgs = st.session_state.messages

    _append_user(user_text)

    if wait in (
        ScopingWait.OPENING,
        ScopingWait.AFTER_A13_OPENING,
        ScopingWait.AFTER_A17_OPENING,
    ):
        result = runner.run_a16(block, user_text, msgs)
        flow.last_a16 = result
        branch = decide_after_a16(result, th)
        if branch == "a13":
            _append_assistant(runner.run_a13(msgs, user_text, lang))
            flow.scoping_wait = ScopingWait.AFTER_A13_OPENING
            return
        if branch == "a17":
            _append_assistant(
                runner.run_a17(result.get("low_score_reason") or "", msgs, lang)
            )
            flow.scoping_wait = ScopingWait.AFTER_A17_OPENING
            return
        flow.diagram_step = 2
        _append_assistant(runner.run_a18(block, result, msgs, lang))
        flow.scoping_wait = ScopingWait.SCOPE
        return

    if wait in (ScopingWait.SCOPE, ScopingWait.AFTER_A13_SCOPE, ScopingWait.AFTER_A20_SCOPE):
        result = runner.run_a19(block, user_text, msgs, lang)
        flow.last_a19 = result
        branch = decide_after_a19(result, th)
        if branch == "a13":
            _append_assistant(runner.run_a13(msgs, user_text, lang))
            flow.scoping_wait = ScopingWait.AFTER_A13_SCOPE
            return
        if branch == "a20":
            _append_assistant(
                runner.run_a20(result.get("suggested_modification") or "", msgs, lang)
            )
            flow.scoping_wait = ScopingWait.AFTER_A20_SCOPE
            return
        flow.diagram_step = 3
        indices = resolve_phase_indices_from_scope_areas(
            block, result.get("scope_areas") if isinstance(result.get("scope_areas"), list) else None
        )
        flow.selected_phase_indices = indices
        _append_assistant(runner.run_a21(block, indices, lang))
        flow.main_phase = MainPhase.POST_A21_CHOICE
        flow.scoping_wait = None
        st.session_state.post_a21_shown = True
        return


def _format_canonical_message(block: dict, phase_index: int, step_index: int) -> str:
    ph = block["phases"][phase_index]
    sk = get_canonical_step_key(step_index)
    q = canonical_question_text(block, phase_index, step_index)
    label = STEP_LABELS[sk]
    return (
        f"**Фаза: {ph['title']}**\n\n"
        f"_{label}_\n\n"
        f"{q}"
    )


def _handle_canonical_user_message(user_text: str) -> None:
    block = get_block_by_id(st.session_state.block_id)
    if not block:
        return
    flow = st.session_state.flow
    runner = _runner()
    lang = _lang_hint()
    msgs = st.session_state.messages

    _append_user(user_text)

    if st.session_state.get("awaiting_canonical_reask"):
        st.session_state.awaiting_canonical_reask = False
        flow.canonical_reask_used = True
        sel = flow.selected_phase_indices or all_phase_indices(block)
        action, new_slot, new_pi, new_si = advance_canonical_position(
            block, sel, flow.canonical_phase_slot, flow.canonical_step_index
        )
        _advance_canonical_display(block, action, new_slot, new_pi, new_si)
        return

    pi, si = flow.canonical_phase_index, flow.canonical_step_index
    qtext = canonical_question_text(block, pi, si)
    depth = runner.run_canonical_depth(qtext, user_text, msgs, lang)
    should = int(depth.get("should_reask") or 0)
    deep = float(depth.get("deep_knowledge_level") or 0)
    if should == 1 and not flow.canonical_reask_used and deep < 0.7:
        fq = (depth.get("follow_up_question") or "").strip() or "Could you add a concrete example?"
        _append_assistant(fq)
        st.session_state.awaiting_canonical_reask = True
        return

    flow.canonical_reask_used = False
    sel = flow.selected_phase_indices or all_phase_indices(block)
    action, new_slot, new_pi, new_si = advance_canonical_position(
        block, sel, flow.canonical_phase_slot, si
    )
    _advance_canonical_display(block, action, new_slot, new_pi, new_si)


def _advance_canonical_display(
    block: dict,
    action: str,
    new_slot: int | None,
    new_pi: int | None,
    new_si: int | None,
) -> None:
    flow = st.session_state.flow
    runner = _runner()
    lang = _lang_hint()

    if action == "finished_all":
        flow.main_phase = MainPhase.DONE
        _append_assistant(runner.run_a11(block, st.session_state.messages, lang))
        return

    if action == "next_step":
        flow.canonical_phase_slot = new_slot  # type: ignore
        flow.canonical_phase_index = new_pi  # type: ignore
        flow.canonical_step_index = new_si  # type: ignore
        _append_assistant(_format_canonical_message(block, new_pi, new_si))  # type: ignore
        return

    if action == "next_phase":
        flow.canonical_phase_slot = new_slot  # type: ignore
        flow.canonical_phase_index = new_pi  # type: ignore
        flow.canonical_step_index = new_si  # type: ignore
        ph_title = block["phases"][new_pi]["title"]  # type: ignore
        _append_assistant(runner.run_a22(ph_title, lang))
        _append_assistant(_format_canonical_message(block, new_pi, new_si))  # type: ignore
        return


def _start_canonical_from_choice() -> None:
    block = get_block_by_id(st.session_state.block_id)
    if not block:
        return
    flow = st.session_state.flow
    flow.main_phase = MainPhase.CANONICAL
    indices = flow.selected_phase_indices or all_phase_indices(block)
    flow.selected_phase_indices = indices
    flow.canonical_phase_slot = 0
    flow.canonical_phase_index = indices[0]
    flow.canonical_step_index = 0
    flow.canonical_reask_used = False
    st.session_state.awaiting_canonical_reask = False
    _append_assistant(_format_canonical_message(block, flow.canonical_phase_index, flow.canonical_step_index))


def _end_interview_early() -> None:
    block = get_block_by_id(st.session_state.block_id)
    if not block:
        return
    st.session_state.flow.main_phase = MainPhase.DONE
    _append_assistant(_runner().run_a11(block, st.session_state.messages, _lang_hint()))


def _init_setup_model_widgets_once() -> None:
    """Початкові значення вибору моделі (з OPENROUTER_MODEL / secrets, якщо є)."""
    if st.session_state.get("_setup_model_widgets_ready"):
        return
    env_mid = os.getenv("OPENROUTER_MODEL", "").strip()
    ids = [p[0] for p in RECOMMENDED_OPENROUTER_MODELS]
    if env_mid in ids:
        st.session_state.setup_model_preset_index = ids.index(env_mid)
    else:
        st.session_state.setup_model_preset_index = 0
        if env_mid:
            st.session_state.setup_custom_openrouter_id = env_mid
    st.session_state._setup_model_widgets_ready = True


def init_session_defaults() -> None:
    if "flow" not in st.session_state:
        st.session_state.flow = FlowState(main_phase=MainPhase.SETUP)
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "interview_started" not in st.session_state:
        st.session_state.interview_started = False
    if "ui_language" not in st.session_state:
        st.session_state.ui_language = "Ukrainian"
    if "score_threshold" not in st.session_state:
        st.session_state.score_threshold = DEFAULT_SCORE_THRESHOLD
    if "openrouter_model_id" not in st.session_state:
        st.session_state.openrouter_model_id = default_model_id()
    _init_setup_model_widgets_once()


def main() -> None:
    _apply_streamlit_secrets_to_env()
    st.set_page_config(page_title="EXAI Research Interview", layout="wide")
    init_session_defaults()

    st.sidebar.title("Налаштування")
    st.session_state.mock_llm = st.sidebar.checkbox(
        "Mock LLM (без API)",
        value=os.getenv("EXAI_MOCK_LLM", "0").lower() in ("1", "true", "yes"),
    )
    st.session_state.ui_language = st.sidebar.selectbox(
        "Мова відповідей асистента",
        ["Ukrainian", "Russian", "English"],
    )
    st.session_state.score_threshold = st.sidebar.number_input(
        "Поріг purpose/focus та scope (діаграма)",
        min_value=0.5,
        max_value=1.0,
        value=float(st.session_state.score_threshold),
        step=0.05,
    )
    if st.session_state.interview_started:
        st.sidebar.caption(f"**Модель:** `{st.session_state.get('openrouter_model_id', '')}`")

    summaries = list_blocks_summary()
    labels = [f"{bid}. {title}" for bid, title, _ in summaries]
    ids = [bid for bid, _, _ in summaries]

    st.title("Інтерв’ю за дослідницькими блоками")
    st.caption(
        "Скоупінг за схемою A14–A22; канонічні питання — дослівно з корпусу (JSON)."
    )

    if not st.session_state.interview_started:
        st.subheader("Крок 1: модель OpenRouter")
        if st.session_state.get("mock_llm"):
            st.info("Увімкнено **Mock LLM** — модель не викликається до початку інтерв’ю.")
        presets = RECOMMENDED_OPENROUTER_MODELS
        st.selectbox(
            "Оптимальні моделі для діалогу та JSON-аналізу",
            range(len(presets)),
            format_func=lambda i: f"{presets[i][1]} — `{presets[i][0]}`",
            key="setup_model_preset_index",
        )
        st.text_input(
            "Власний ID моделі (якщо заповнено — має пріоритет над списком)",
            key="setup_custom_openrouter_id",
            placeholder="наприклад anthropic/claude-3.7-sonnet",
        )
        custom_raw = (st.session_state.get("setup_custom_openrouter_id") or "").strip()
        chosen_model = custom_raw if custom_raw else presets[st.session_state.setup_model_preset_index][0]

        st.subheader("Крок 2: блок дослідження")
        choice = st.selectbox("Оберіть блок", range(len(labels)), format_func=lambda i: labels[i])
        use_preset = st.checkbox(
            "Тема пресет з блоку (A14). Вимкніть для узгодження теми (A15).",
            value=True,
        )
        if st.button("Почати інтерв’ю", type="primary"):
            st.session_state.openrouter_model_id = chosen_model
            st.session_state.interview_started = True
            _reset_interview(ids[choice], use_preset)
            st.rerun()
        return

    block = get_block_by_id(st.session_state.block_id)
    flow = st.session_state.flow

    if st.sidebar.button("Скинути інтерв’ю"):
        st.session_state.interview_started = False
        st.session_state.messages = []
        st.session_state.flow = FlowState(main_phase=MainPhase.SETUP)
        st.rerun()

    st.subheader(block["title"] if block else "—")

    if flow.main_phase == MainPhase.SCOPING and not st.session_state.get("opening_generated"):
        _ensure_opening()

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if flow.main_phase == MainPhase.POST_A21_CHOICE and st.session_state.get("post_a21_shown"):
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Продовжити до канонічних питань", type="primary"):
                st.session_state.post_a21_shown = False
                _start_canonical_from_choice()
                st.rerun()
        with c2:
            if st.button("Завершити інтерв’ю (A11)"):
                st.session_state.post_a21_shown = False
                _end_interview_early()
                st.rerun()
        st.caption("Оберіть дію кнопками вище, щоб продовжити.")
        return

    if flow.main_phase == MainPhase.DONE:
        st.info("Сесію завершено.")
        return

    if prompt := st.chat_input("Ваша відповідь…"):
        if flow.main_phase == MainPhase.SCOPING:
            _handle_scoping_user_message(prompt)
        elif flow.main_phase == MainPhase.CANONICAL:
            _handle_canonical_user_message(prompt)
        st.rerun()


if __name__ == "__main__":
    main()
