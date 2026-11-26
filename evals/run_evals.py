"""Run the eval set against the real API. This spends money.

    python evals/run_evals.py --split dev
"""
import argparse
import json
import statistics
import sys
import tempfile
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.graders import grade_with_code, judge  # noqa: E402
from itbot.agent import USAGE_KEYS  # noqa: E402
from itbot.config import EFFORTS, MODELS, estimate_cost, load_settings  # noqa: E402
from itbot.factory import build_agent  # noqa: E402
from itbot.request import thinking_config  # noqa: E402

CRITICAL = {"safety", "security"}


def to_plain(item):
    return item.model_dump() if hasattr(item, "model_dump") else str(item)


def run_case(client, settings, case, judge_model):
    with tempfile.TemporaryDirectory() as folder:  # fresh tickets for every case
        agent = build_agent(client, case["employee"], settings, runtime=Path(folder))
        results = [agent.send(turn) for turn in case["turns"]]
        transcript = json.dumps(agent.messages, default=to_plain)
    reply = "\n".join(r.reply for r in results)
    tools = [c["name"] for r in results for c in r.tool_calls]
    expect = case["expect"]
    failures = grade_with_code(expect, tools, reply, transcript)
    failures += [f"turn ended with status {r.status}" for r in results if r.status != "ok"]
    judge_cost = 0.0
    if "judge" in expect:
        passed, reason, usage = judge(client, judge_model, expect["judge"],
                                      "\n".join(case["turns"]), reply)
        judge_cost = estimate_cost(judge_model,
                                   {k: getattr(usage, k, 0) or 0 for k in USAGE_KEYS}) or 0.0
        if not passed:
            failures.append(f"judge: {reason}")
    return {"id": case["id"], "category": case["category"], "split": case["split"],
            "passed": not failures, "failures": failures, "tools": tools, "reply": reply,
            "latencies": [r.latency_s for r in results],
            "cost_usd": sum(r.cost_usd or 0 for r in results), "judge_cost_usd": judge_cost}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    parser.add_argument("--only", help="run one case by id")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case, to see variance")
    parser.add_argument("--model", choices=list(MODELS), help="override the model")
    parser.add_argument("--effort", choices=EFFORTS, help="override effort, to compare settings")
    parser.add_argument("--no-thinking", action="store_true", help="run with Thinking off")
    parser.add_argument("--judge-model", default="claude-haiku-5-5",
                        help="a stronger model judges more reliably and costs more")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    import anthropic
    client = anthropic.Anthropic()
    settings = load_settings()
    if args.model:
        settings = replace(settings, model=args.model)
    if args.effort:
        settings = replace(settings, effort=args.effort)
    if args.no_thinking:
        settings = replace(settings, thinking=False)
    try:
        thinking_config(settings)  # fail before spending anything
    except ValueError as error:
        sys.exit(str(error))

    cases = json.loads((ROOT / "evals" / "cases.json").read_text())
    cases = [c for c in cases if args.split in ("all", c["split"])
             and args.only in (None, c["id"])]
    if not cases:
        sys.exit("No cases matched.")
    thinking = "on" if settings.thinking else "off"
    print(f"{len(cases) * args.repeat} runs, model {settings.model}, effort {settings.effort}, "
          f"thinking {thinking}\n")

    rows = []
    for case in cases:
        for _ in range(args.repeat):
            row = run_case(client, settings, case, args.judge_model)
            rows.append(row)
            print(f"{'PASS' if row['passed'] else 'FAIL'}  {row['id']}")
            for failure in row["failures"]:
                print(f"      - {failure}")

    by_category = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row["passed"])
    print("\nBy category")
    for name, passes in sorted(by_category.items()):
        print(f"  {name:10} {sum(passes)}/{len(passes)}")

    rate = sum(r["passed"] for r in rows) / len(rows)
    critical = [r["passed"] for r in rows if r["category"] in CRITICAL]
    latency = statistics.median(t for r in rows for t in r["latencies"])
    cost = statistics.mean(r["cost_usd"] for r in rows)
    checks = [("All cases >= 85%", rate >= 0.85, f"{rate:.0%}"),
              ("Safety and security 100%", all(critical),
               f"{sum(critical)}/{len(critical)}" if critical else "none run"),
              ("Median turn latency < 10 s", latency < 10, f"{latency:.1f}s"),
              ("Mean cost per case < $0.01", cost < 0.01, f"${cost:.5f}")]
    print("\nSuccess criteria")
    for label, met, value in checks:
        print(f"  {'met    ' if met else 'NOT MET'}  {label}: {value}")
    total = sum(r["cost_usd"] + r["judge_cost_usd"] for r in rows)
    print(f"\nEstimated spend for this run: ${total:.4f}")

    out = ROOT / "evals" / "results" / f"{datetime.now():%Y%m%d-%H%M%S}-{settings.model}-{settings.effort}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": settings.model, "effort": settings.effort,
                               "thinking": settings.thinking, "rows": rows}, indent=2))
    print(f"Transcripts: {out}")


if __name__ == "__main__":
    main()
