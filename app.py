"""
Streamlit app: block selection, scoping agents A11–A22, canonical questions from JSON.
"""

from __future__ import annotations

import json
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
    """Streamlit Community Cloud: copy st.secrets into os.environ for the OpenRouter client."""
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
    ):
        if key in sec:
            val = sec[key]
            if val is not None and str(val).strip():
                os.environ[key] = str(val).strip()


# Fail fast if corpus is invalid
validate_research_blocks()

STEP_LABELS = {
    "general": "Step 1 (General)",
    "deepening": "Step 2 (Deepening)",
    "drilling": "Step 3 (Drilling)",
}

RESPONSE_LANGUAGE_OPTIONS = [
    "English",
    "Ukrainian",
    "Russian",
    "German",
    "French",
    "Spanish",
    "Polish",
    "Italian",
]


def _runner() -> AgentRunner:
    return AgentRunner(
        model=st.session_state.get("openrouter_model_id"),
        temperature=float(st.session_state.get("llm_temperature", 0.1)),
        log_sink=st.session_state.get("agent_logs"),
    )


def _lang_hint() -> str:
    return str(st.session_state.get("response_language") or "English")


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
    st.session_state.awaiting_canonical_reask = False
    st.session_state.agent_logs = []
    st.session_state.current_canonical_question_plain = ""


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
        text = runner.run_a15(block, lang)
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
        result = runner.run_a16(block, user_text, msgs, lang)
        flow.last_a16 = result
        branch = decide_after_a16(result, th)
        if branch == "a13":
            _append_assistant(runner.run_a13(msgs, user_text, lang, block=block))
            flow.scoping_wait = ScopingWait.AFTER_A13_OPENING
            return
        if branch == "a17":
            _append_assistant(
                runner.run_a17(result.get("low_score_reason") or "", msgs, lang, block=block)
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
            _append_assistant(runner.run_a13(msgs, user_text, lang, block=block))
            flow.scoping_wait = ScopingWait.AFTER_A13_SCOPE
            return
        if branch == "a20":
            _append_assistant(
                runner.run_a20(result.get("suggested_modification") or "", msgs, lang, block=block)
            )
            flow.scoping_wait = ScopingWait.AFTER_A20_SCOPE
            return
        flow.diagram_step = 3
        indices = resolve_phase_indices_from_scope_areas(
            block, result.get("scope_areas") if isinstance(result.get("scope_areas"), list) else None
        )
        flow.selected_phase_indices = indices
        bridge = runner.run_a21(block, indices, lang).strip()
        if bridge and bridge != ".":
            _append_assistant(bridge)
        flow.main_phase = MainPhase.CANONICAL
        flow.canonical_phase_slot = 0
        flow.canonical_phase_index = indices[0]
        flow.canonical_step_index = 0
        flow.canonical_reask_used = False
        st.session_state.awaiting_canonical_reask = False
        flow.scoping_wait = None
        _append_assistant(
            _display_canonical_question(block, flow.canonical_phase_index, flow.canonical_step_index)
        )
        return


def _display_canonical_question(block: dict, phase_index: int, step_index: int) -> str:
    ph = block["phases"][phase_index]
    sk = get_canonical_step_key(step_index)
    flow = st.session_state.flow
    a16 = flow.last_a16 or {}
    focus = str(a16.get("extracted_focus_area") or "").strip()
    q = _runner().run_synthesize_canonical_question(
        block,
        phase_index,
        step_index,
        focus,
        st.session_state.messages,
        _lang_hint(),
    ).strip()
    st.session_state.current_canonical_question_plain = q
    label = STEP_LABELS[sk]
    pid = ph.get("phase_id", "")
    pid_line = f"`{pid}` · " if pid else ""
    return (
        f"**Phase: {pid_line}{ph['title']}**\n\n"
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
    qtext = (st.session_state.get("current_canonical_question_plain") or "").strip()
    if not qtext:
        a16 = flow.last_a16 or {}
        focus = str(a16.get("extracted_focus_area") or "").strip()
        qtext = runner.run_synthesize_canonical_question(
            block, pi, si, focus, msgs, lang
        ).strip()
        st.session_state.current_canonical_question_plain = qtext
    depth = runner.run_canonical_depth(
        qtext,
        user_text,
        msgs,
        lang,
        block=block,
        phase_index=pi,
        step_key=get_canonical_step_key(si),
    )
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
        _append_assistant(_display_canonical_question(block, new_pi, new_si))  # type: ignore
        return

    if action == "next_phase":
        flow.canonical_phase_slot = new_slot  # type: ignore
        flow.canonical_phase_index = new_pi  # type: ignore
        flow.canonical_step_index = new_si  # type: ignore
        _append_assistant(runner.run_a22(block, new_pi, lang))  # type: ignore
        _append_assistant(_display_canonical_question(block, new_pi, new_si))  # type: ignore
        return


def _init_setup_model_widgets_once() -> None:
    """One-time defaults for model widgets from OPENROUTER_MODEL / secrets."""
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
    if "score_threshold" not in st.session_state:
        st.session_state.score_threshold = DEFAULT_SCORE_THRESHOLD
    if "openrouter_model_id" not in st.session_state:
        st.session_state.openrouter_model_id = default_model_id()
    if "llm_temperature" not in st.session_state:
        st.session_state.llm_temperature = 0.1
    if "agent_logs" not in st.session_state:
        st.session_state.agent_logs = []
    if "response_language" not in st.session_state:
        st.session_state.response_language = "English"
    if "current_canonical_question_plain" not in st.session_state:
        st.session_state.current_canonical_question_plain = ""
    _init_setup_model_widgets_once()


def main() -> None:
    _apply_streamlit_secrets_to_env()
    st.set_page_config(page_title="EXAI Research Interview", layout="wide")
    init_session_defaults()

    st.sidebar.title("Settings")
    st.session_state.response_language = st.sidebar.selectbox(
        "Assistant response language",
        RESPONSE_LANGUAGE_OPTIONS,
        index=RESPONSE_LANGUAGE_OPTIONS.index(st.session_state.response_language)
        if st.session_state.response_language in RESPONSE_LANGUAGE_OPTIONS
        else 0,
        help="All LLM agents are instructed to reply only in this language (system prompt suffix).",
    )
    st.session_state.llm_temperature = st.sidebar.slider(
        "LLM temperature",
        min_value=0.0,
        max_value=2.0,
        value=float(st.session_state.llm_temperature),
        step=0.05,
        help="Applied to every OpenRouter chat completion (default 0.1).",
    )
    st.session_state.score_threshold = st.sidebar.number_input(
        "Purpose/focus and scope agreement threshold (diagram)",
        min_value=0.5,
        max_value=1.0,
        value=float(st.session_state.score_threshold),
        step=0.05,
    )
    if st.session_state.interview_started:
        st.sidebar.caption(f"**Model:** `{st.session_state.get('openrouter_model_id', '')}`")
        logs = st.session_state.get("agent_logs") or []
        with st.sidebar.expander("Agent pipeline logs", expanded=False):
            st.caption(
                "Each entry: system prompt + user payload sent to the model, and raw response "
                "(JSON agents also store `output_parsed`)."
            )
            st.metric("Recorded LLM calls", len(logs))
            if logs:
                st.download_button(
                    label="Download logs (JSON)",
                    data=json.dumps(logs, ensure_ascii=False, indent=2),
                    file_name="agent_pipeline_logs.json",
                    mime="application/json",
                    key="download_agent_logs",
                )
                for i, entry in enumerate(logs):
                    agent = entry.get("agent", "?")
                    ts = (entry.get("timestamp_utc") or "")[:19]
                    with st.expander(f"{i + 1}. {agent} @ {ts}Z"):
                        inp = entry.get("input") or {}
                        st.markdown("**System prompt**")
                        st.text(inp.get("system_prompt", ""))
                        st.markdown("**User message**")
                        st.text(inp.get("user_message", ""))
                        st.markdown("**Model output (raw)**")
                        st.text(entry.get("output_raw", ""))
                        if entry.get("output_parsed") is not None:
                            st.markdown("**Parsed output**")
                            st.json(entry["output_parsed"])
            else:
                st.info("No API calls recorded yet in this session.")

    summaries = list_blocks_summary()
    labels = [f"{bid}. {title}" for bid, title, _ in summaries]
    ids = [bid for bid, _, _ in summaries]

    st.title("Research-block expert interview")
    st.caption(
        "Scoping flow A14–A22; interview questions are **synthesized** from JSON step briefs using the respondent’s stated role and domain (example job titles in the block are indicative only)."
    )

    if not st.session_state.interview_started:
        st.subheader("Step 1: OpenRouter model")
        presets = RECOMMENDED_OPENROUTER_MODELS
        st.selectbox(
            "Recommended models for dialogue and JSON-style scoring",
            range(len(presets)),
            format_func=lambda i: f"{presets[i][1]} — `{presets[i][0]}`",
            key="setup_model_preset_index",
        )
        st.text_input(
            "Custom model ID (if set, overrides the list above)",
            key="setup_custom_openrouter_id",
            placeholder="e.g. anthropic/claude-3.7-sonnet",
        )
        custom_raw = (st.session_state.get("setup_custom_openrouter_id") or "").strip()
        chosen_model = custom_raw if custom_raw else presets[st.session_state.setup_model_preset_index][0]

        st.subheader("Step 2: research block")
        choice = st.selectbox("Choose block", range(len(labels)), format_func=lambda i: labels[i])
        use_preset = st.checkbox(
            "Use block title as preset topic (A14). Uncheck to negotiate topic first (A15).",
            value=True,
        )
        if st.button("Start interview", type="primary"):
            st.session_state.openrouter_model_id = chosen_model
            st.session_state.interview_started = True
            _reset_interview(ids[choice], use_preset)
            st.rerun()
        return

    block = get_block_by_id(st.session_state.block_id)
    flow = st.session_state.flow

    if st.sidebar.button("Reset interview"):
        st.session_state.interview_started = False
        st.session_state.messages = []
        st.session_state.agent_logs = []
        st.session_state.flow = FlowState(main_phase=MainPhase.SETUP)
        st.rerun()

    st.subheader(block["title"] if block else "—")

    if flow.main_phase == MainPhase.SCOPING and not st.session_state.get("opening_generated"):
        _ensure_opening()

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if flow.main_phase == MainPhase.DONE:
        st.info("Session completed.")
        return

    if prompt := st.chat_input("Your message…"):
        if flow.main_phase == MainPhase.SCOPING:
            _handle_scoping_user_message(prompt)
        elif flow.main_phase == MainPhase.CANONICAL:
            _handle_canonical_user_message(prompt)
        st.rerun()


if __name__ == "__main__":
    main()
