"""Chat UI. Run with:  streamlit run app.py"""
import os
from dataclasses import replace

import streamlit as st

from itbot.config import (EFFORTS, HELPDESK, MAX_INSTRUCTION_CHARS, MODELS, ROOT,
                          load_settings, thinking_can_be_disabled)
from itbot.factory import build_agent, load_employees

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

st.set_page_config(page_title="Lumen Harbor IT Support", page_icon="🛟")
state = st.session_state
defaults = load_settings()
employees = load_employees()


def make_client():
    if "client_override" in state:  # used by the UI tests
        return state.client_override
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def new_conversation(employee_id, settings):
    state.agent = build_agent(make_client(), employee_id, settings,
                              runtime=state.get("runtime_override"))
    state.chat = []
    state.totals = {"tokens": 0, "cost": 0.0}


st.title("🛟 IT Support")

if make_client() is None:
    st.warning("No API key found. Copy `.env.example` to `.env`, add your "
               "`ANTHROPIC_API_KEY`, then restart the app.")
    st.caption(HELPDESK)
    st.stop()

with st.sidebar:
    # Stands in for single sign-on. In production the user could not choose this.
    employee_id = st.selectbox("Signed in as (stands in for company sign-in)", list(employees),
                               format_func=lambda i: employees[i]["name"])
    reset = st.button("New conversation")

    st.subheader("Model settings")
    model = st.selectbox("Model", list(MODELS), index=list(MODELS).index(defaults.model),
                         format_func=lambda m: MODELS[m].label, key="model")
    price_in, price_out, _ = MODELS[model].prices
    st.caption(f"${price_in:.2f} input / ${price_out:.2f} output per million tokens")
    effort = st.selectbox("Effort", EFFORTS, index=EFFORTS.index(defaults.effort), key="effort",
                          help="Higher effort can think and use more tokens before answering.")
    if thinking_can_be_disabled(model, effort):
        thinking = st.toggle("Thinking", value=True, key="thinking")
        if not thinking and MODELS[model].thinking_off == "between_tools":
            st.caption("Off is sent as `between_tools` on this model.")
    else:
        # A separate key, so a saved "off" cannot carry over to a combination that rejects it.
        thinking = st.toggle("Thinking", value=True, disabled=True, key="thinking_locked")
        st.caption("This model cannot turn Thinking off." if MODELS[model].thinking_off is None
                   else "To turn Thinking off, choose effort high or lower.")
    instruction = st.text_area(
        "System instruction", key="instruction", max_chars=MAX_INSTRUCTION_CHARS,
        placeholder="For example: Answer in no more than three sentences.",
        help="Added after the built-in IT support prompt. Press Ctrl+Enter, or click "
             "outside the box, to apply.")
    st.caption("Changing the user or any model setting starts a new chat.")

settings = replace(defaults, model=model, effort=effort, thinking=thinking,
                   instruction=instruction.strip())

# Switching user starts over, so one person's chat never reaches another. A settings
# change does too: earlier thinking blocks may not be valid under the new settings.
if (reset or "agent" not in state or state.agent.context.employee_id != employee_id
        or state.agent.settings != settings):
    new_conversation(employee_id, settings)
agent = state.agent


def show_trace(result):
    with st.expander("Agent trace"):
        for call in result.tool_calls:
            st.markdown(f"**{call['name']}**" + (" (error)" if call["is_error"] else ""))
            st.json(call["input"])
        cost = "unknown" if result.cost_usd is None else f"${result.cost_usd:.5f}"
        st.caption(f"{result.status} · {result.latency_s}s · est. cost {cost} · "
                   f"tokens {sum(result.usage.values())}")
        for flag in result.flags:
            st.warning(flag)
        if result.request:
            st.markdown("**Request sent to Claude**")
            st.json(result.request, expanded=1)


for entry in state.chat:
    with st.chat_message(entry["role"]):
        st.markdown(entry["text"])
        result = entry.get("result")
        if result:
            if result.sources:
                st.caption("Sources: " + ", ".join(
                    f"{a.id} {a.title}" for a in agent.context.kb.articles
                    if a.id in result.sources))
            show_trace(result)

prompt = st.chat_input("Describe your IT problem", max_chars=settings.max_input_chars)
if prompt:
    from itbot.guardrails import redact
    shown_prompt = redact(prompt.strip())[0]
    with st.chat_message("user"):
        st.markdown(shown_prompt)
    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_Working on it..._")
        result = agent.send(prompt, on_text=placeholder.markdown)
    state.chat += [{"role": "user", "text": shown_prompt},
                   {"role": "assistant", "text": result.reply, "result": result}]
    state.totals["tokens"] += sum(result.usage.values())
    state.totals["cost"] += result.cost_usd or 0.0
    st.rerun()

for draft_id, draft in agent.context.drafts.items():
    if draft["state"] != "pending":
        continue
    with st.container(border=True):
        st.subheader("Review ticket draft")
        st.text(f"{draft['title']}\nCategory: {draft['category']}   "
                f"Priority: {draft['priority']}\n")
        st.text(draft["description"])
        left, right = st.columns(2)
        if left.button("Confirm and file", key=f"ok-{draft_id}", type="primary"):
            ticket = agent.confirm_draft(draft_id)
            state.chat.append({"role": "assistant", "text": f"Ticket **{ticket['id']}** filed."})
            st.rerun()
        if right.button("Discard", key=f"no-{draft_id}"):
            agent.discard_draft(draft_id)
            state.chat.append({"role": "assistant", "text": "Draft discarded. Nothing was filed."})
            st.rerun()

# Drawn last so it reflects anything that changed during this run.
with st.sidebar:
    st.subheader("My tickets")
    for ticket in agent.context.tickets.for_owner(employee_id):
        st.text(f"{ticket['id']} · {ticket['status']}\n{ticket['title']}")
    st.divider()
    st.caption(f"{settings.model} · effort {settings.effort} · thinking "
               f"{'on' if settings.thinking else 'off'}")
    st.caption(f"This conversation: {state.totals['tokens']} tokens, "
               f"est. ${state.totals['cost']:.5f}")
    st.caption(HELPDESK)
