#!/usr/bin/env python3
"""Batch runner for the LLM4HLS Track A harness.

Runs the agent over a WHOLE task set with one or more OpenRouter models,
with checkpoint/resume, a failed-task retry pass (retry_ids), and a
FINAL_SUMMARY.md per model — i.e. the infrastructure behind the report in
your screenshot that the upstream repo does not ship.

Usage (from the repo root, inside the Vitis 2025.2 environment):

  export OPENROUTER_API_KEY=sk-or-...
  python scripts/run_batch.py \
      --tasks-dir tasks_all \
      --models "qwen/qwen3.5-122b-a10b" \
      --repair-rounds 10 --opt-rounds 5 \
      --retry-failed --resume

Outputs under runs_batch/<model_slug>/:
  results.json        checkpoint after every task (safe to Ctrl-C / resume)
  FINAL_SUMMARY.md    coverage / success / retry_ids / audit, per model
  <task_id>/          per-task agent + grading work dirs and final kernel
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows console default (gbk) cannot encode unicode output; force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from llm4hls import Budget, ReferenceAgent, ToolServer, grade, load_task
from llm4hls.llm import OpenRouterClient

ROOT = Path(__file__).resolve().parent.parent


def discover_tasks(tasks_dir: Path) -> list[Path]:
    dirs = sorted(p for p in tasks_dir.iterdir() if (p / "task.toml").exists())
    if not dirs:
        raise SystemExit(f"no task packages (task.toml) found under {tasks_dir}")
    return dirs


def run_one(
    task_dir: Path,
    model: str,
    args,
    work_root: Path,
) -> dict:
    """Run agent + grading for a single task. Never raises — errors are
    captured into the result dict so one bad task can't kill the batch."""
    tid = task_dir.name
    try:
        task = load_task(task_dir)
        tid = task.id
        total = args.budget if args.budget else task.budget
        budget = Budget(total=total)
        llm = OpenRouterClient(model=model)
        server = ToolServer(task, budget, work_root / tid / "agent")
        agent = ReferenceAgent(
            task,
            server,
            llm,
            repair_rounds=args.repair_rounds,
            opt_rounds=args.opt_rounds,
        )
        t0 = time.time()
        final = agent.run()
        card = grade(task, final, work_root / tid / "grade")
        (work_root / tid / f"final_{task.kernel_name}").write_text(final, encoding="utf-8")
        return {
            "task_id": tid,
            "type": task.type,
            "difficulty": task.difficulty,
            "functional_pass": card.functional_pass,
            "synth_pass": card.synth_pass,
            "cosim_pass": card.cosim_pass,
            "score": card.score,
            "acceleration": card.acceleration,
            "credits_spent": budget.spent,
            "budget": total,
            "wall_s": round(time.time() - t0, 1),
        }
    except Exception as e:  # noqa: BLE001 - record and continue
        return {
            "task_id": tid,
            "functional_pass": False,
            "synth_pass": False,
            "cosim_pass": None,
            "score": 0.0,
            "acceleration": None,
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc()[-2000:],
        }


def write_summary(
    model: str,
    results: dict[str, dict],
    n_tasks: int,
    out_path: Path,
    started: float,
) -> None:
    covered = len(results)
    passed = sorted(t for t, r in results.items() if r.get("functional_pass"))
    retry_ids = sorted(t for t, r in results.items() if not r.get("functional_pass"))
    total_score = sum(r.get("score", 0.0) for r in results.values())
    hrs = (time.time() - started) / 3600

    lines = [
        f"# FINAL_SUMMARY — {model}",
        "",
        f"- date: {datetime.now():%Y-%m-%d %H:%M}",
        f"- result: **{len(passed)}/{n_tasks} success**, retry_ids={retry_ids}",
        f"- coverage: {covered}/{n_tasks} "
        f"({'OK' if covered == n_tasks else 'INCOMPLETE — rerun with --resume'})",
        f"- real_api_only: True (backend=OpenRouterClient, per-task clients)",
        f"- total score (correctness-gated PPA): {total_score:.2f}",
        f"- wall time: {hrs:.2f} h",
        "",
        "## Failed / incomplete tasks",
        "",
        "| task_id | type | error / phase | score |",
        "|---|---|---|---|",
    ]
    for tid in retry_ids:
        r = results[tid]
        lines.append(
            f"| {tid} | {r.get('type', '?')} | "
            f"{r.get('error', 'agent did not reach correctness')} | "
            f"{r.get('score', 0.0)} |"
        )
    lines += [
        "",
        "## All results",
        "",
        "| task_id | pass | synth | score | accel | credits | wall_s |",
        "|---|---|---|---|---|---|---|",
    ]
    for tid in sorted(results):
        r = results[tid]
        accel = r.get("acceleration")
        lines.append(
            f"| {tid} | {'PASS' if r.get('functional_pass') else 'FAIL'} "
            f"| {r.get('synth_pass')} | {r.get('score', 0.0)} "
            f"| {f'{accel:.2f}x' if accel else '-'} "
            f"| {r.get('credits_spent', '-')}/{r.get('budget', '-')} "
            f"| {r.get('wall_s', '-')} |"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_model(model: str, task_dirs: list[Path], args) -> None:
    slug = model.replace("/", "_").replace(":", "_")
    out_root = ROOT / "runs_batch" / slug
    out_root.mkdir(parents=True, exist_ok=True)
    results_path = out_root / "results.json"

    results: dict[str, dict] = {}
    if args.resume and results_path.exists():
        results = json.loads(results_path.read_text(encoding="utf-8"))
        print(f"[{model}] resumed {len(results)} existing results")

    started = time.time()
    pending = [
        d for d in task_dirs
        if not (args.resume and results.get(d.name, {}).get("functional_pass"))
        # note: results are keyed by task.id; dir name == id in the standard layout
    ]

    def save() -> None:
        results_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def do_batch(dirs: list[Path], tag: str) -> None:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {
                pool.submit(run_one, d, model, args, out_root): d for d in dirs
            }
            for i, fut in enumerate(as_completed(futs), 1):
                res = fut.result()
                results[res["task_id"]] = res
                save()  # checkpoint after EVERY task
                mark = "PASS" if res.get("functional_pass") else "FAIL"
                print(
                    f"[{model}] {tag} {i}/{len(dirs)} {res['task_id']}: {mark} "
                    f"(score {res.get('score', 0.0)})",
                    flush=True,
                )

    print(f"[{model}] main pass: {len(pending)} tasks to run")
    do_batch(pending, "main")

    # retry pass: re-run every failed task once (fresh agent, same budget).
    # This is what turns "API 异常/偶发失败" into retry_ids=[] in the report.
    if args.retry_failed:
        failed = [
            d for d in task_dirs
            if not results.get(d.name, {}).get("functional_pass")
        ]
        if failed:
            print(f"[{model}] retry pass: {len(failed)} failed tasks")
            do_batch(failed, "retry")

    write_summary(model, results, len(task_dirs), out_root / "FINAL_SUMMARY.md", started)
    n_ok = sum(1 for r in results.values() if r.get("functional_pass"))
    retry_ids = sorted(t for t, r in results.items() if not r.get("functional_pass"))
    print(
        f"[{model}] DONE: {n_ok}/{len(task_dirs)} success, retry_ids={retry_ids}\n"
        f"  summary -> {out_root / 'FINAL_SUMMARY.md'}",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", default="tasks_all",
                    help="directory containing task packages (default: tasks_all)")
    ap.add_argument("--models", required=True,
                    help="comma-separated OpenRouter model slugs")
    ap.add_argument("--budget", type=int, default=None,
                    help="override per-task credit budget")
    ap.add_argument("--repair-rounds", type=int, default=10)
    ap.add_argument("--opt-rounds", type=int, default=5)
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel tasks per model (Vitis is heavy: 2-4 max)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-run failed tasks once after the main pass")
    ap.add_argument("--resume", action="store_true",
                    help="skip tasks that already passed in results.json")
    args = ap.parse_args()

    task_dirs = discover_tasks(Path(args.tasks_dir))
    print(f"discovered {len(task_dirs)} tasks under {args.tasks_dir}")
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        run_model(model, task_dirs, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())