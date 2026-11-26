# Design

```
Streamlit UI (app.py)
   | signed-in employee (set by the app, never by the model)
   v
Input guardrails: length cap, secret redaction
   v
Agent loop (itbot/agent.py)  <-->  Claude Messages API (model chosen in sidebar)
   |  tools (itbot/tools.py)
   |    search_knowledge_base   kb/*.md, BM25 keyword search
   |    check_service_status    data/service_status.json
   |    list_my_tickets / get_ticket   scoped to the employee
   |    draft_ticket            draft only; UI button files it
   |    escalate_to_human       files an urgent handoff at once
   v
Output guardrails: strip images/outside links, check citations
   v
logs/turns.jsonl: tools, tokens, estimated cost, latency, flags
```

## Decisions
| Decision | Reason | Revisit when |
|---|---|---|
| One agent with six tools | Simplest design that does all four jobs | Tool count or domains grow |
| Messages API with a hand-written loop | Runs locally; every step is visible for learning | Moving to a hosted deployment |
| Keyword (BM25) search, no vector database | Ten short articles; easy to debug | Users' wording misses articles in evals |
| Draft then confirm for tickets | Human approves the write action | Never for risky writes |
| Escalation needs no confirmation | A missed incident costs more than a false alarm | False alarms swamp the queue |
| Default Haiku 5.5, effort `high`; others selectable | Chosen for cost; support rules need instruction following | Compare effort and models with evals |
| System instruction is appended to the built-in prompt | Keeps support and safety rules | Never in production without review |
| A settings change starts a new chat | Earlier thinking blocks may be invalid under new settings | n/a |
| Append-only history | Haiku 5.5 thinking blocks must be replayed unchanged | n/a |

## Iteration loop
Run the evals, read the failing transcripts, name the cause, change one thing, rerun.
Tune on the `dev` cases only. Check `holdout` cases to see whether a fix generalizes.

## Before a real pilot
Real SSO and ticketing, a reviewed knowledge base, a data retention decision,
rate limits, better PII detection, and a human-reviewed sample of live conversations.
