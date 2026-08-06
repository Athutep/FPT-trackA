"""Task-package format + loader.

A task directory is the unit a competitor is handed. Layout:

    <task>/
      task.toml            # spec: top fn, types, budget, target, difficulty
      description.md       # natural-language spec / interface contract
      <kernel>.cpp         # the STARTING code given to the agent (broken/slow)
      <kernel>.h           # header(s) -- fixed, agent may not change
      <kernel>_tb.cpp      # PUBLIC testbench (agent may csim against it, metered)
      hidden/<kernel>_tb.cpp   # HIDDEN testbench (grader only) -- optional
      reference/<kernel>.cpp   # golden solution (offline scoring baseline + scripted agent)

Only <kernel>.cpp is under the agent's control; the headers and testbenches are
fixed by contract, so a submission is judged on the kernel alone.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass
class Task:
    dir: Path
    id: str
    type: str  # generate | repair | optimize | synth_fix
    difficulty: int
    top: str
    budget: int
    part: str
    clock_ns: float
    requires_cosim: bool
    initial_condition: str
    description: str
    kernel_name: str
    kernel_code: str  # starting code handed to the agent
    headers: dict = field(default_factory=dict)  # name -> content (fixed)
    public_tb_name: str = ""
    public_tb_code: str = ""
    hidden_tb_name: str = ""
    hidden_tb_code: str = ""
    reference_code: str | None = None  # golden kernel, if provided

    def assemble(self, kernel_code: str, tb_code: str, tb_name: str) -> dict:
        """Full file set (headers + given kernel + a testbench) for a tool run."""
        files = dict(self.headers)
        files[self.kernel_name] = kernel_code
        files[tb_name] = tb_code
        return files


def load_task(task_dir: str | Path) -> Task:
    d = Path(task_dir).resolve()
    spec = tomllib.loads((d / "task.toml").read_text(encoding="utf-8"))
    target = spec.get("target", {})

    kernel_name = spec["kernel_file"]
    headers = {h: (d / h).read_text(encoding="utf-8") for h in spec.get("header_files", [])}

    public_tb_name = spec["public_tb"]
    public_tb_code = (d / public_tb_name).read_text(encoding="utf-8")

    hidden_tb_name = spec.get("hidden_tb", public_tb_name)
    hidden_path = d / "hidden" / hidden_tb_name
    if hidden_path.exists():
        hidden_tb_code = hidden_path.read_text(encoding="utf-8")
    else:
        hidden_tb_code = public_tb_code  # fallback: reuse the public bench

    ref_path = d / "reference" / kernel_name
    reference_code = ref_path.read_text(encoding="utf-8") if ref_path.exists() else None

    return Task(
        dir=d,
        id=spec.get("task_id", d.name),
        type=spec.get("task_type", "generate"),
        difficulty=int(spec.get("difficulty", 1)),
        top=spec["top"],
        budget=int(spec.get("budget", 40)),
        # LLM4HLS_PART overrides the task's pinned part (e.g. for local dev
        # on a different board than the competition target).
        part=(
            os.environ.get("LLM4HLS_PART")
            or target.get("part")
            or config.DEFAULT_PART
        ),
        clock_ns=float(target.get("clock_ns", 5.0)),
        requires_cosim=bool(spec.get("requires_cosim", False)),
        initial_condition=spec.get("initial_condition", ""),
        description=(
            (d / "description.md").read_text(encoding="utf-8")
            if (d / "description.md").exists()
            else ""
        ),
        kernel_name=kernel_name,
        kernel_code=(d / kernel_name).read_text(encoding="utf-8"),
        headers=headers,
        public_tb_name=public_tb_name,
        public_tb_code=public_tb_code,
        hidden_tb_name=hidden_tb_name,
        hidden_tb_code=hidden_tb_code,
        reference_code=reference_code,
    )
