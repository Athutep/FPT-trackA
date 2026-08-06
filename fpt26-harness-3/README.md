# LLM4HLS Track A — Reference Agent & Evaluation Harness

This is a minimal, self-contained example implementation of **Track A** of the LLM4HLS
design contest ("Budgeted End-to-End LLM4HLS Agent").


## Note

**This is only an example implementation of the Track A reference agent and evaluation harness. It is not a competition submission. Competitors are free to implement their own agent and evaluation harness, as long as they satisfy the requirements of the contest.**

**We strongly recommend including a maximum token budget or execution time limit as a configurable parameter in your project, rather than relying on exhaustive search. We will enforce a reasonable time and token consumption limit during evaluation.**

We also provided an example [`Dockerfile`](./vitis.dockerfile) to set up a Vitis 2025.2 environment with the required dependencies for running the reference agent and evaluation harness.

```
task package  ──►  metered csim / synth / cosim tools (credit budget)  ──►
   correctness-before-PPA agent loop  ──►  hidden-test grading + PPA scorecard
```

- **Tools:** `csim`, `synth`, and `cosim` (C/RTL co-simulation, required for
  deadlock / invalid-streaming structural tasks).
- **LLM provider:** OpenRouter, **open-source models only** (contest rule). No
  proprietary-model backend is shipped.
- **Target:** Vitis **2025.2** (HLS via `vitis-run --mode hls`), **Alveo
  U55C** `xcu55c-fsvh2892-2L-e`, **200 MHz** (5 ns).

## Layout
```
llm4hls/
  config.py    toolchain + target constants (env-overridable)
  vitis.py     low-level `vitis-run --mode hls --tcl <tcl>` runner
  tools.py     CSimTool (two-step compile/run) + SynthTool + CoSimTool
  report.py    report parsers: csynth.xml (latency / II / LUT-FF-DSP-BRAM-URAM)
               and <top>_cosim.rpt (RTL-verified status + measured latency)
  budget.py    credit meter (csim=1, synth=4, cosim=20); BudgetExceeded on overrun
  task.py      task-package format + loader
  harness.py   ToolServer: the metered, audited agent-facing tool API
  scoring.py   hidden-test grading + correctness-gated PPA scorecard
  llm.py       pluggable LLM backend: OpenRouterClient | ScriptedClient (offline)
  agent.py     ReferenceAgent: iterative repair -> synth -> optimize loop
tasks/
  projection_bugfix/        repair    (compiles + synths, fails csim: functional bug)
  dotProduct_optimize/      optimize  (correct but unoptimized baseline)
  residual_stream_deadlock/ structural(passes csim, DEADLOCKS in cosim: dataflow FIFO burst)
scripts/run_poc.py       end-to-end driver
```

## Task package format
A task directory contains the following files:
```
task.toml            spec: top fn, task_type, difficulty, budget, [target] part/clock
description.md       natural-language spec / interface contract
<kernel>.cpp         the STARTING code (broken or slow) — the only file the agent edits
<kernel>.h           header(s), fixed by contract
<kernel>_tb.cpp      PUBLIC testbench (agent may csim against it, metered)
hidden/<kernel>_tb.cpp   HIDDEN testbench (grader only; optional, falls back to public)
reference/<kernel>.cpp   golden solution (baseline PPA + offline scripted agent)
```
`task_type` ∈ `generate | repair | optimize | synth_fix`

## Running
Requires Vitis **2025.2** reachable at `LLM4HLS_VITIS_HLS_ROOT`
(default `/opt/xilinx/2025.2/Vitis`; HLS is driven by `vitis-run --mode hls`,
since 2025.2 no longer ships the standalone `vitis_hls`).

```bash
conda activate fpt26

# offline: the scripted backend replays the golden reference/ solution
python scripts/run_poc.py tasks/projection_bugfix
python scripts/run_poc.py tasks/dotProduct_optimize

# real agent, open-source model via OpenRouter
export OPENROUTER_API_KEY=...        # or fill it into llm4hls/config.py
export LLM4HLS_MODEL=qwen/qwen-2.5-coder-32b-instruct   # any open model
python scripts/run_poc.py tasks/dotProduct_optimize --backend openrouter
```

## The metered interface
`ToolServer` exposes three budget-charged calls; each returns a structured
result (`phase` ∈ pass / compile_error / runtime_fail / synth_error / cosim_fail /
timeout, plus a parsed report) and is appended to an audit transcript:
```python
server.csim(kernel_code)  -> ToolResult   # costs 1 credit
server.synth(kernel_code) -> ToolResult   # costs 4 credits, .report has PPA
server.cosim(kernel_code) -> ToolResult   # costs 20 credits, RTL-verified; .cosim has measured latency
```
The agent only ever supplies the kernel source; the headers and testbench are
fixed by the harness, so a submission is judged on the kernel alone.

## Scoring (correctness before PPA)
`scoring.grade()` runs the **hidden** testbench and synthesis outside the budget:
```
if not hidden_TB_pass:  score = 0            # correctness gate
else:
    Acceleration = baseline_latency / candidate_latency
    ppa_norm     = min(Acceleration, 8) / 8
    score        = difficulty * (0.5*correct + 0.2*synthesizable + 0.3*ppa_norm)
```
There is no power figure at C-synthesis; resource usage stands in for "P".

## Co-simulation (structural tasks)
Setting `requires_cosim = true` in a task's `task.toml` makes cosim part of the
agent's correctness phase (not just a final check) and a grading gate — so a
design that passes C-sim but **deadlocks** (Vitis's deadlock detector →
`cosim_fail`, or a genuine hang → `timeout`) or has an **RTL/C mismatch**
(→ `cosim_fail`) is correctly failed.

`residual_stream_deadlock` is the reference case: a DATAFLOW residual connection
whose producer writes one full stream before the other. C-sim (unbounded FIFOs)
passes; cosim (bounded depth-2 FIFOs) deadlocks. The agent sees the `cosim_fail`,
rebalances the stream writes, and both pass. A representative run:
```
#1 [csim]  pass                     -> csim ALONE would accept the broken design
#2 [cosim] cosim_fail  (deadlock)   -> cosim catches it
#3 [csim]  pass        (fixed)
#4 [cosim] pass        (fixed)      -> correctness reached (csim+cosim)
```
