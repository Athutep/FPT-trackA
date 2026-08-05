"""The metered tool interface exposed to a competitor's agent.

This is the competition's evaluation interface: the agent may only touch the
kernel source; the headers and the public testbench are fixed by the harness.
Every call is charged against the Budget and appended to a transcript, so the
run is fully auditable. Grading against the hidden testbench happens in
scoring.py, outside this metered surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import Budget
from .task import Task
from .tools import CoSimTool, CSimTool, SynthTool, ToolResult


@dataclass
class TranscriptEntry:
    n: int
    kind: str
    phase: str
    spent: int
    detail: str


class ToolServer:
    def __init__(self, task: Task, budget: Budget, run_root: Path) -> None:
        self.task = task
        self.budget = budget
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._csim = CSimTool()
        self._synth = SynthTool()
        self._cosim = CoSimTool()
        self.transcript: list[TranscriptEntry] = []
        self._n = 0

    def _record(self, r: ToolResult) -> None:
        self._n += 1
        self.transcript.append(
            TranscriptEntry(self._n, r.kind, r.phase, self.budget.spent, r.brief())
        )

    def csim(self, kernel_code: str) -> ToolResult:
        """Compile+run the PUBLIC testbench against `kernel_code`. Charged."""
        self.budget.charge("csim")
        files = self.task.assemble(
            kernel_code, self.task.public_tb_code, self.task.public_tb_name
        )
        r = self._csim.run(
            self.run_root / f"csim_{self._n + 1}",
            files,
            top=self.task.top,
            part=self.task.part,
            clock_ns=self.task.clock_ns,
        )
        self._record(r)
        return r

    def synth(self, kernel_code: str) -> ToolResult:
        """C-synthesize `kernel_code`. Charged. Returns a parsed PPA report."""
        self.budget.charge("synth")
        files = dict(self.task.headers)
        files[self.task.kernel_name] = kernel_code
        r = self._synth.run(
            self.run_root / f"synth_{self._n + 1}",
            files,
            synth_sources=[self.task.kernel_name],
            top=self.task.top,
            part=self.task.part,
            clock_ns=self.task.clock_ns,
        )
        self._record(r)
        return r

    def cosim(self, kernel_code: str) -> ToolResult:
        """C/RTL co-simulate `kernel_code` against the PUBLIC testbench. Charged."""
        self.budget.charge("cosim")
        files = self.task.assemble(
            kernel_code, self.task.public_tb_code, self.task.public_tb_name
        )
        r = self._cosim.run(
            self.run_root / f"cosim_{self._n + 1}",
            files,
            synth_sources=[self.task.kernel_name],
            tb_sources=[self.task.public_tb_name],
            top=self.task.top,
            part=self.task.part,
            clock_ns=self.task.clock_ns,
        )
        self._record(r)
        return r
