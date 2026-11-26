# Lumen Harbor IT support assistant

A local IT support chatbot built as a Forward Deployed Engineer training exercise: a
tool-using Claude agent with a Streamlit UI. The company and all data are made up.

## Run it
Requires Python 3 (built with 3.14) and a Claude API key. API calls cost money.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then put your key after ANTHROPIC_API_KEY=
.venv/bin/streamlit run app.py
```

Open http://localhost:8501. Run commands from this folder.
The app runs locally, but your messages and tool results are sent to the Claude API.

If a message fails, open **Agent trace** under the reply to see the API's reason.
"Credit balance is too low" means the API account needs credit (Plans & Billing in the
Claude Console).

## Chat settings (sidebar)
| Setting | What it does |
|---|---|
| Model | Haiku 5.5 (default), Sonnet 5.5 or Opus 5.5 |
| Effort | `low`, `medium`, `high` (default), `xhigh`, `max` |
| Thinking | On by default. Off needs effort `high` or lower, and is not possible on Opus 5.5 |
| System instruction | Added after the built-in IT support prompt, not in place of it |

`itbot/request.py` combines these with your message for every API call. Open **Agent
trace** and then **Request sent to Claude** to see what was sent. It shows the first call
of a turn; later calls in the same turn also carry the tool results.

Changing a setting starts a new chat. Sonnet 5.5 and Opus 5.5 requests turn on the
server-side refusal fallback (a beta feature), so another model may answer if a safety
filter declines. The trace says when that happens, and the cost may then show as unknown.

In a real product, employees would not get these controls. Here they are for learning.

## Test it
Offline tests (no key, no cost):

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

Evals (real API calls):

```bash
.venv/bin/python evals/run_evals.py --split dev
```

Useful options: `--only phishing-clicked`, `--repeat 3`, `--effort low`,
`--split holdout`, `--judge-model claude-sonnet-5-5`, `--model claude-sonnet-5-5`,
`--no-thinking`.

The offline tests check the code. Only the evals check how Claude behaves.

## The FDE workflow, and where it lives
| Step | File |
|---|---|
| Discovery, scope, success criteria, risks | `docs/01_discovery.md` |
| Architecture and decisions | `docs/02_design.md` |
| Prompt: context and the reasons behind rules | `itbot/prompts.py` |
| Tool design and least privilege | `itbot/tools.py` |
| Grounding in approved content | `itbot/knowledge.py`, `kb/` |
| Agent loop and failure handling | `itbot/agent.py` |
| Guardrails in code | `itbot/guardrails.py` |
| Human in the loop | Draft then Confirm in `app.py`; escalation tool |
| Observability and cost | `itbot/telemetry.py`, `runtime/turns.jsonl` |
| Evals with dev and holdout splits | `evals/` |
| Customer demo | `docs/03_demo_script.md` |

## Exercises
1. Run the dev evals, read each failure transcript in `evals/results/`, fix one cause, rerun.
2. Compare `--effort low`, `medium` and `high` on pass rate, latency and cost.
3. Add an article and an eval case for it.
4. Write cases that try to break a guardrail.

## Known limits
- The user picker stands in for sign-in. Anyone using the app can choose either person.
- Secret redaction is pattern-based and will miss some secrets.
- Image and link filtering is a basic filter, not a full Markdown sanitizer.
- Unknown citations are flagged. The code does not check that each claim matches its article.
- `runtime/turns.jsonl` stores redacted chat text with no retention limit.
- Keyword search can miss questions worded very differently from the articles.
- The eval set is small, so one run is not a reliable score. Use `--repeat`.
- Prices in `itbot/config.py` are a snapshot; check current pricing.
