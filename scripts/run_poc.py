#!/usr/bin/env python3
"""End-to-end driver: run the reference agent on a task under a credit budget,
then grade the result with the hidden testbench and print a PPA scorecard.

Usage:
    python scripts/run_poc.py tasks/projection_bugfix
    python scripts/run_poc.py tasks/dotProduct_optimize --backend scripted
    python scripts/run_poc.py tasks/dotProduct_optimize --backend openrouter --budget 60

The 'scripted' backend replays the task's golden reference/ solution, so the
whole harness runs offline with no token. The 'openrouter' backend drives a
real open-source model via OpenRouter (needs OPENROUTER_API_KEY).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows console default (gbk) cannot encode unicode output; force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from llm4hls import Budget, ReferenceAgent, ToolServer, grade, load_task
from llm4hls.llm import OpenRouterClient, ScriptedClient

POC_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_dir")
    ap.add_argument("--backend", choices=["scripted", "openrouter"], default="scripted")
    ap.add_argument(
        "--budget", type=int, default=None, help="override task budget (credits)"
    )
    ap.add_argument(
        "--work", default=None, help="working dir root (default: <root>/runs/<task>)"
    )
    args = ap.parse_args()

    task = load_task(args.task_dir)
    total = args.budget if args.budget is not None else task.budget
    budget = Budget(total=total)

    work_root = Path(args.work) if args.work else POC_ROOT / "runs" / task.id
    run_root = work_root / "agent"
    grade_root = work_root / "grade"

    if args.backend == "openrouter":
        llm = OpenRouterClient()
    else:
        if task.reference_code is None:
            print(
                "scripted backend requires a reference/ solution in the task",
                file=sys.stderr,
            )
            return 1
        llm = ScriptedClient(["```cpp\n" + task.reference_code + "```"])

    print(f"=== Task {task.id} [{task.type}, difficulty {task.difficulty}] ===")
    print(
        f"    target: {task.part} @ {task.clock_ns} ns | budget: {total} credits | backend: {args.backend}"
    )
    print(f"    initial condition: {task.initial_condition}\n")

    server = ToolServer(task, budget, run_root)
    agent = ReferenceAgent(task, server, llm)
    final = agent.run()

    print("\n--- metered tool transcript ---")
    for e in server.transcript:
        print(f"  #{e.n:<2} {e.detail}   [spent {e.spent}/{total}]")
    print(f"  {budget.summary()}")

    print("\n=== Grading (hidden testbench + PPA, uncharged) ===")
    card = grade(task, final, grade_root)
    print(card.render())

    out = work_root / f"final_{task.kernel_name}"
    out.write_text(final, encoding="utf-8")
    print(f"\nfinal kernel -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
