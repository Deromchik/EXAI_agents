"""
Streamlit app: block selection, scoping agents A11–A22, canonical questions from JSON.
"""

from __future__ import annotations

import hashlib
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
    decide_after_a16,
    get_canonical_step_key,
)
from state_machine import DEFAULT_SCORE_THRESHOLD
from interview_session import (
    SESSION_EXPORT_FORMAT,
    apply_block_to_import_result,
    build_session_export_dict,
    parse_session_object,
    session_import_result_to_state_updates,
)

load_dotenv()


def _conversation_export_txt(messages: list[dict]) -> str:
    """Plain-text export: one block per turn (assistant / user)."""
    blocks: list[str] = []
    for m in messages:
        role = (m.get("role") or "?").strip().upper()
        content = (m.get("content") or "").strip()
        blocks.append(f"[{role}]\n{content}")
    return "\n\n".join(blocks)


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


def _sync_setup_model_index_from_session() -> None:
    mid = st.session_state.get("openrouter_model_id")
    ids = [p[0] for p in RECOMMENDED_OPENROUTER_MODELS]
    if isinstance(mid, str) and mid in ids:
        st.session_state.setup_model_preset_index = ids.index(mid)


def _reset_interview(block_id: int) -> None:
    st.session_state.flow = FlowState(
        main_phase=MainPhase.SCOPING,
        diagram_step=1,
        scoping_wait=None,
        score_threshold=st.session_state.get("score_threshold", DEFAULT_SCORE_THRESHOLD),
        selected_phase_indices=[],
        canonical_phase_index=0,
        canonical_phase_slot=0,
        canonical_step_index=0,
        last_a16=None,
    )
    st.session_state.messages = []
    st.session_state.block_id = block_id
    st.session_state.opening_generated = False
    st.session_state.awaiting_canonical_reask = False
    st.session_state.agent_logs = []
    st.session_state.current_canonical_question_plain = ""
    st.session_state.pop("_session_resume_handled_digest", None)


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
    text = runner.run_a14(block, lang)
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
        # After A18, go straight to canonical with all phases (no separate scope-negotiation step).
        flow.diagram_step = 3
        indices = all_phase_indices(block)
        flow.selected_phase_indices = indices
        flow.main_phase = MainPhase.CANONICAL
        flow.canonical_phase_slot = 0
        flow.canonical_phase_index = indices[0]
        flow.canonical_step_index = 0
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
    focus_ext = str(a16.get("extended_focus_area") or "").strip()
    q = _runner().run_synthesize_canonical_question(
        block,
        phase_index,
        step_index,
        focus,
        focus_ext,
        st.session_state.messages,
        _lang_hint(),
    ).strip()
    st.session_state.current_canonical_question_plain = q
    label = STEP_LABELS[sk]
    return (
        f"**Phase: {ph['title']}**\n\n"
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

    pi, si = flow.canonical_phase_index, flow.canonical_step_index
    qtext = (st.session_state.get("current_canonical_question_plain") or "").strip()
    if not qtext:
        a16 = flow.last_a16 or {}
        focus = str(a16.get("extracted_focus_area") or "").strip()
        focus_ext = str(a16.get("extended_focus_area") or "").strip()
        qtext = runner.run_synthesize_canonical_question(
            block, pi, si, focus, focus_ext, msgs, lang
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
    if should == 1 and deep < 0.7:
        fq = (depth.get("follow_up_question") or "").strip() or (
            "That was still quite general. Could you give one concrete example from your own practice?"
        )
        _append_assistant(fq)
        st.session_state.awaiting_canonical_reask = True
        return

    st.session_state.awaiting_canonical_reask = False
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
    """One-time defaults for model selectbox from OPENROUTER_MODEL / secrets (whitelist only)."""
    if st.session_state.get("_setup_model_widgets_ready"):
        return
    ids = [p[0] for p in RECOMMENDED_OPENROUTER_MODELS]
    resolved = default_model_id()
    st.session_state.setup_model_preset_index = ids.index(resolved)
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
    msgs = list(st.session_state.get("messages") or [])
    with st.sidebar.expander("Session backup & resume", expanded=False):
        st.caption(
            f"**JSON ({SESSION_EXPORT_FORMAT})** — same file for **download and upload**: "
            "messages, `block_id`, flow state, `position` (phase_id, step_key, pending_turn), "
            "language, model, temperature. TXT export is human-readable only (no resume)."
        )
        st.metric("Chat messages", len(msgs))
        bid = st.session_state.get("block_id")
        base = f"session_block{bid}" if bid is not None else "session"
        export_obj = None
        if st.session_state.interview_started and bid is not None:
            blk = get_block_by_id(int(bid))
            fl = st.session_state.flow
            if isinstance(fl, FlowState) and blk is not None:
                export_obj = build_session_export_dict(
                    messages=msgs,
                    block_id=int(bid),
                    flow=fl,
                    opening_generated=bool(st.session_state.get("opening_generated")),
                    awaiting_canonical_reask=bool(st.session_state.get("awaiting_canonical_reask")),
                    current_canonical_question_plain=str(
                        st.session_state.get("current_canonical_question_plain") or ""
                    ),
                    response_language=str(st.session_state.get("response_language") or "English"),
                    openrouter_model_id=st.session_state.get("openrouter_model_id"),
                    llm_temperature=float(st.session_state.get("llm_temperature", 0.1)),
                    block=blk,
                )
        st.download_button(
            label="Download session (JSON)",
            data=json.dumps(export_obj or {"format": SESSION_EXPORT_FORMAT, "messages": []}, ensure_ascii=False, indent=2),
            file_name=f"{base}.json",
            mime="application/json",
            key="download_session_json",
            disabled=export_obj is None,
        )
        st.download_button(
            label="Download conversation (TXT)",
            data=_conversation_export_txt(msgs),
            file_name=f"{base}.txt",
            mime="text/plain; charset=utf-8",
            key="download_conversation_txt",
            disabled=len(msgs) == 0,
        )
        up = st.file_uploader(
            "Resume from session JSON",
            type=["json"],
            help=f"Use a file from “Download session (JSON)” ({SESSION_EXPORT_FORMAT}).",
            key="session_resume_uploader",
        )
        if up is not None:
            raw = up.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            if st.session_state.get("_session_resume_handled_digest") != digest:
                try:
                    obj = json.loads(raw.decode("utf-8"))
                    result = parse_session_object(obj)
                    if result.legacy_messages_only:
                        st.warning(result.warnings[0])
                        st.session_state._session_resume_handled_digest = digest
                    else:
                        block = get_block_by_id(result.block_id)
                        if not block:
                            st.error(
                                f"No research block with block_id={result.block_id} in research_blocks.json."
                            )
                            st.session_state._session_resume_handled_digest = digest
                        else:
                            root = obj if isinstance(obj, dict) else {}
                            extra_warn = apply_block_to_import_result(result, block, root)
                            for key, val in session_import_result_to_state_updates(result).items():
                                st.session_state[key] = val
                            _sync_setup_model_index_from_session()
                            for w in result.warnings + extra_warn:
                                st.warning(w)
                            st.success("Session restored. Continue in the chat.")
                            st.session_state._session_resume_handled_digest = digest
                            st.rerun()
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    st.error(f"Invalid JSON: {e}")
                    st.session_state._session_resume_handled_digest = digest
                except ValueError as e:
                    st.error(str(e))
                    st.session_state._session_resume_handled_digest = digest
                except Exception as e:
                    st.error(f"Could not restore session: {e}")
                    st.session_state._session_resume_handled_digest = digest

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
        "Scoping A14–A18, bridges A22; questions **synthesized** by CANONICAL_Q from block/phase/focus (example job titles in the block are indicative only)."
    )

    if not st.session_state.interview_started:
        st.subheader("Step 1: OpenRouter model")
        presets = RECOMMENDED_OPENROUTER_MODELS
        st.selectbox(
            "Model (allowed list only)",
            range(len(presets)),
            format_func=lambda i: f"{presets[i][1]} — `{presets[i][0]}`",
            key="setup_model_preset_index",
        )
        chosen_model = presets[st.session_state.setup_model_preset_index][0]

        st.subheader("Step 2: research block")
        choice = st.selectbox("Choose block", range(len(labels)), format_func=lambda i: labels[i])
        if st.button("Start interview", type="primary"):
            st.session_state.pop("_session_resume_handled_digest", None)
            st.session_state.openrouter_model_id = chosen_model
            st.session_state.interview_started = True
            _reset_interview(ids[choice])
            st.rerun()
        return

    block = get_block_by_id(st.session_state.block_id)
    flow = st.session_state.flow

    if st.sidebar.button("Reset interview"):
        st.session_state.interview_started = False
        st.session_state.messages = []
        st.session_state.agent_logs = []
        st.session_state.flow = FlowState(main_phase=MainPhase.SETUP)
        st.session_state.pop("_session_resume_handled_digest", None)
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
