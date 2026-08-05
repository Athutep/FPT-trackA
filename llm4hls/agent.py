"""Reference LLM4HLS agent — accuracy-hardened with strategic enhancements.

Enhancements vs the accuracy-hardened version:
- Testbench output structured parsing: extracts failed test cases, expected vs actual values
- Error type classification: categorizes errors for targeted fix strategies
- Context accumulation: preserves recent repair history so the LLM avoids repeated mistakes
- Task-type adaptive prompts: different guidance for repair / optimize / structural / synth_fix
- Multi-stage reasoning: instructs the LLM to analyze before coding (chain-of-thought)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .budget import BudgetExceeded
from .harness import ToolServer
from .llm import LLMClient
from .report import SynthReport
from .task import Task
from .tools import ToolResult

# ── Pattern sets ────────────────────────────────────────────────────────────

_CODE_RE = re.compile(r"```(?:cpp|c\+\+|c)?\s*\n(.*?)```", re.DOTALL)

_ERR_HINTS = (
    "error", "Error", "ERROR", "failed", "FAILED", "Fatal", "undefined",
    "mismatch", "Mismatch", "MISMATCH", "assert", "deadlock", "Deadlock",
    "timeout", "Timeout", "segmentation", "exception",
)

# Extract structured test-failure information from testbench stdout.
# Matches patterns like:
#   "Test Case 1 Failed!"
#   "Mismatch at 5: expected 42, got 17"
#   "Test failed! Expected: 1.234, Got: 5.678"
#   "expected 10, got 5"
#   "3 test case(s) failed!"
_TESTB_FAIL_RE = re.compile(
    r"(?:"
    r"(?:Test|test)\s*(?:Case|case)?\s*(\d+)\s*(?:Failed|failed)"
    r"|Mismatch\s*(?:at|@)\s*(\d+)"
    r"|Expected:\s*([^,;]+)\s*[,;]\s*(?:Got|got|Received):\s*(.+)"
    r"|expected\s+([^,\s]+)\s*,\s*got\s+(\S+)"
    r"|(\d+)\s+test\s+(?:case|cases|case\(s\))?\s*failed"
    r")",
    re.IGNORECASE,
)

# Tool phase -> error type mapping
_PHASE_TO_ERRTYPE = {
    "compile_error": "COMPILE_ERROR",
    "runtime_fail":  "RUNTIME_MISMATCH",
    "timeout":       "TIMEOUT",
    "synth_error":   "SYNTH_ERROR",
    "cosim_fail":    "COSIM_FAIL",
}

_ERR_TYPE_DESC = {
    "COMPILE_ERROR":    "Compilation failed — fix syntax, type, or missing #include errors.",
    "RUNTIME_MISMATCH": "Wrong results — the kernel logic does not match the testbench specification.",
    "TIMEOUT":          "Simulation timed out — likely an infinite loop or a dataflow deadlock.",
    "SYNTH_ERROR":      "C-synthesis failed — remove unsynthesizable constructs (malloc, recursion, system calls, etc.).",
    "COSIM_FAIL":       "C/RTL co-simulation failed — RTL behaviour does not match C (stream/dataflow hazard).",
    "DEADLOCK":         "RTL simulation deadlocked — unbounded stream writes overflow bounded FIFOs.",
}

# ── System prompt template ──────────────────────────────────────────────────

_SYSTEM_BASE = """You are an expert FPGA/HLS engineer working with AMD Vitis 2025.2, \
targeting an Alveo U55C at 200 MHz (5 ns clock). You iteratively write and optimize \
synthesizable HLS C++ kernels. Rules:
- Output ONLY the full contents of the kernel .cpp file, inside a single ```cpp fenced block.
- Do NOT change the top-level function signature, the header, or the testbench.
- Keep the code functionally correct first; optimize for latency second.
- Prefer general HLS techniques (PIPELINE, UNROLL, ARRAY_PARTITION, DATAFLOW) over hacks.
- Common pitfalls to avoid: reading/writing hls::stream outside a loop that the \
testbench expects, changing array sizes declared in the header, using dynamic \
memory or recursion (not synthesizable), and forgetting #include <ap_int.h> / \
<hls_stream.h> when using those types."""

# These guidance snippets are appended for the specific task type.
_TASK_GUIDANCE = {
    "repair": (
        "\nTask type: REPAIR — the kernel compiles but has a functional bug. "
        "Analyze the testbench failure to identify which output is wrong, "
        "trace the logic in the corresponding branch, and fix the computation."
    ),
    "optimize": (
        "\nTask type: OPTIMIZE — the kernel is correct but has high latency. "
        "Apply HLS optimization pragmas (PIPELINE with II=1, UNROLL, "
        "ARRAY_PARTITION cyclic/complete, DATAFLOW) and/or restructure loops "
        "to reduce latency while preserving exact functionality."
    ),
    "structural": (
        "\nTask type: STRUCTURAL — the kernel has a streaming/dataflow hazard. "
        "Ensure each hls::stream producer writes at the same rate its consumer "
        "reads, so that bounded RTL FIFOs (depth 2) never overflow. "
        "If a stage drives two streams, write to both in the SAME loop iteration."
    ),
    "synth_fix": (
        "\nTask type: SYNTH_FIX — the kernel fails C-synthesis. "
        "Remove or replace constructs that are not synthesizable: dynamic "
        "memory allocation (malloc/new), recursion, virtual functions, "
        "system calls (printf/fopen), floating-point in unsupported contexts, "
        "or unsized arrays."
    ),
}

MAX_HISTORY = 3

# Hardware quality (QHW): performance weight, area weight, timing weight per mode.
_QHW_WEIGHTS = {
    "balanced": (0.55, 0.30, 0.15),
    "speed":    (0.75, 0.15, 0.10),
    "area":     (0.40, 0.50, 0.10),
}

# ── Hardware optimization knowledge base (3 layers, from the reference teams) ─

# Arithmetic layer: CSD + MCM + CSE for constant multiplications.
_CSD_MCM_CSE_GUIDANCE = (
    "Arithmetic optimization (constant multiplications): if the kernel "
    "multiplies variables by compile-time constants, replace the multiplier "
    "with a sparse shift-add network using CSD (Canonic Signed Digit) "
    "encoding, e.g. y = x*13 = (x<<4) - (x<<2) + x. For multiple constants, "
    "share common subexpressions (CSE / MCM) across them to reuse adder "
    "networks and cut DSP/LUT usage."
)

# Data representation layer: bitwidth reduction via range analysis.
_BITWIDTH_GUIDANCE = (
    "Bitwidth optimization: analyze the data range of each INTERNAL variable "
    "(propagate ranges through +, -, * and shifts) and reduce ap_int/ap_fixed "
    "widths to the smallest size that cannot overflow, keeping 1-2 guard bits "
    "of headroom. IMPORTANT: never change the top-level function signature or "
    "any type declared in the header (the interface is fixed); only shrink "
    "internal/local types, loop counters and intermediate accumulators. "
    "Smaller widths cut DSP/LUT usage and can improve Fmax."
)

# Architecture layer hint (in addition to the dynamic bottleneck analysis).
_ARCH_LAYER_HINT = (
    "Architecture layer: consider PIPELINE with II=1, UNROLL inner loops, "
    "ARRAY_PARTITION (cyclic/complete) to widen memory bandwidth, DATAFLOW "
    "for task-level parallelism, and partial-sum tree reduction for dot "
    "products / accumulation chains."
)


# ── Helper functions ────────────────────────────────────────────────────────

def _extract_code(text: str) -> str | None:
    blocks = _CODE_RE.findall(text)
    if blocks:
        return blocks[0].strip() + "\n"
    stripped = text.strip()
    if stripped and ("#include" in stripped or "void " in stripped):
        return stripped + "\n"
    return None


def _lat(r: ToolResult) -> int | None:
    if r.report is None:
        return None
    return (
        r.report.latency_worst
        if r.report.latency_worst is not None
        else r.report.latency_avg
    )


def _report_lat(report: SynthReport | None) -> int | None:
    """Latency of a SynthReport (worst else average)."""
    if report is None:
        return None
    return report.latency_worst if report.latency_worst is not None else report.latency_avg


def _report_fmax(report: SynthReport | None) -> float | None:
    """Achieved Fmax (MHz) from a SynthReport's estimated clock period."""
    if report is None or report.clock_period_ns is None or report.clock_period_ns <= 0:
        return None
    return 1000.0 / report.clock_period_ns


def _parse_testbench_output(log: str) -> str:
    """Extract structured test-failure information from testbench stdout.

    Returns a human-readable summary like:
      "Test case 1 FAILED | Mismatch at index 5: expected 42, got 17"
    or an empty string if no failures are detected.
    """
    matches = _TESTB_FAIL_RE.findall(log)
    if not matches:
        return ""

    details = []
    for m in matches:
        test_num, idx, exp_got_a, exp_got_b, exp_simple, got_simple, count = m
        if test_num:
            details.append(f"Test case {test_num} FAILED")
        if idx:
            details.append(f"Mismatch at index {idx}")
        if exp_got_a and exp_got_b:
            details.append(f"Expected: {exp_got_a.strip()}, Got: {exp_got_b.strip()}")
        if exp_simple and got_simple:
            details.append(f"expected {exp_simple.strip()}, got {got_simple.strip()}")
        if count:
            details.append(f"{count} test(s) failed")

    return " | ".join(details) if details else ""


def _classify_error(r: ToolResult) -> str:
    """Map a ToolResult to a high-level error category string."""
    if r.phase == "timeout" and r.kind == "cosim":
        return "DEADLOCK"
    return _PHASE_TO_ERRTYPE.get(r.phase, f"UNKNOWN_{r.phase}")


def _truncate_mid(text: str, head: int = 3000, tail: int = 2000) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + f"\n... [{len(text) - head - tail} chars] ...\n" + text[-tail:]


# ── Agent ───────────────────────────────────────────────────────────────────

@dataclass
class _AttemptRecord:
    attempt: int
    code: str
    error_type: str
    error_summary: str


class ReferenceAgent:
    def __init__(
        self,
        task: Task,
        server: ToolServer,
        llm: LLMClient,
        max_rounds: int = 6,
        repair_rounds: int = 10,
        opt_rounds: int = 5,
        repair_temperature: float = 0.1,
        opt_temperature: float = 0.3,
        max_ask_retries: int = 3,
        verbose: bool = True,
    ) -> None:
        self.task = task
        self.server = server
        self.llm = llm
        self.max_rounds = max_rounds
        self.repair_rounds = repair_rounds
        self.opt_rounds = opt_rounds
        self.repair_temperature = repair_temperature
        self.opt_temperature = opt_temperature
        self.max_ask_retries = max_ask_retries
        self.verbose = verbose

        # Accumulated repair history (last MAX_HISTORY attempts).
        self._history: list[_AttemptRecord] = []

    # -- utilities ---------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [agent] {msg}", flush=True)

    def _afford(self, kind: str) -> bool:
        return self.server.budget.can_afford(kind)

    def _afford_round(self, kinds: list[str]) -> bool:
        return self.server.budget.can_afford_round(kinds)

    def _compute_phase_budgets(self) -> tuple[int, int]:
        """Estimate max repair and optimization rounds the credit budget allows.

        Returns (max_repair_rounds, max_opt_rounds) based on:
        - total budget minus mandatory reserves (baseline synth, cosim if structural)
        - task type: repair-heavy vs opt-heavy allocation
        - explicit round caps from constructor params as upper bounds
        """
        cost = self.server.budget.cost
        total = self.server.budget.total

        mandatory = cost["synth"]
        if self.task.requires_cosim:
            mandatory += cost["cosim"]

        if total <= mandatory:
            return (1, 0)

        remaining = total - mandatory
        repair_unit = cost["csim"]
        opt_unit = cost["csim"] + cost["synth"]

        alloc = {
            "repair":     (0.75, 0.25),
            "optimize":   (0.15, 0.85),
            "structural": (0.60, 0.40),
            "synth_fix":  (0.70, 0.30),
        }
        repair_frac, opt_frac = alloc.get(self.task.type, (0.50, 0.50))

        repair_bgt = int(remaining * repair_frac)
        opt_bgt = remaining - repair_bgt

        max_repair = min(repair_bgt // repair_unit, self.repair_rounds)
        max_opt = min(opt_bgt // opt_unit, self.opt_rounds)

        return (max(1, max_repair), max(0, max_opt))

    def _header_text(self) -> str:
        return "\n".join(f"// {n}\n{c}" for n, c in self.task.headers.items())

    def _task_type_guidance(self) -> str:
        return _TASK_GUIDANCE.get(self.task.type, "")

    def _system_prompt(self) -> str:
        return _SYSTEM_BASE + self._task_type_guidance()

    def _build_history_text(self) -> str:
        if not self._history:
            return ""
        lines = ["## Previous repair attempts (read-only)"]
        for h in self._history:
            lines.append(
                f"  Attempt {h.attempt}: "
                f"error={h.error_type} — {h.error_summary}"
            )
        lines.append(
            "Do NOT repeat the same approach. Learn from what failed before."
        )
        return "\n".join(lines)

    # -- optimization analysis ----------------------------------------------

    def _analyze_bottleneck(self, report: SynthReport) -> tuple[str, str]:
        """Analyze a synthesis report and identify the primary bottleneck.

        Returns (bottleneck_tag, targeted_guidance) for the LLM.
        """
        ii = report.interval_max
        lat = report.latency_worst
        util = report.utilization

        guidances = []

        # II bottleneck: pipelining is not fully effective
        if ii is not None and ii > 1:
            guidances.append(
                f"HIGH_II: Initiation Interval = {ii}, means a new iteration "
                f"starts only every {ii} cycles. Target: reduce II to 1. "
                "Check for loop-carried dependencies (RAW/WAW hazards), "
                "remove data dependencies between iterations, or use "
                "#pragma HLS DEPENDENCE to break false dependencies."
            )

        # Latency bottleneck
        if lat is not None and (ii is None or ii <= 1) and lat > 1000:
            guidances.append(
                f"HIGH_LATENCY: {lat} cycles with II=1. "
                "Further II reduction is impossible; use UNROLL on inner loops "
                "for parallel execution or DATAFLOW for task-level parallelism."
            )
        elif lat is not None and ii is not None and ii > 1 and lat > ii * 200:
            guidances.append(
                f"HIGH_LATENCY: {lat} cycles at II={ii}. "
                "II alone won't solve this — try UNROLL, ARRAY_PARTITION, or "
                "DATAFLOW for coarse-grained parallelism."
            )

        # Resource bottlenecks
        if util.get("DSP", 0) > 30:
            guidances.append(
                f"HIGH_DSP: {util['DSP']:.0f}% DSP usage. "
                "Consider LUT-based multipliers (ALLOCATION pragma) or reducing "
                "parallelism degree."
            )
        if util.get("BRAM_18K", 0) > 40:
            guidances.append(
                f"HIGH_BRAM: {util['BRAM_18K']:.0f}% BRAM usage. "
                "Try ARRAY_PARTITION complete for small arrays, or cyclic "
                "with higher factor to reduce BRAM port contention."
            )
        if util.get("LUT", 0) > 70:
            guidances.append(
                f"HIGH_LUT: {util['LUT']:.0f}% LUT usage. "
                "Consider resource sharing or reducing unroll factors."
            )

        if guidances:
            tag = " | ".join(g.split(":")[0] for g in guidances)
            return (tag, "\n\n".join(guidances))
        return ("ON_TRACK", "No critical bottleneck detected. Try further latency reduction via loop merging, deeper pipelining, or DATAFLOW task-level parallelism.")

    def _ppa_report_text(self, report: SynthReport, prefix: str = "") -> str:
        """Format a synthesis report as a concise PPA summary for LLM prompts."""
        lat = report.latency_worst if report.latency_worst is not None else "?"
        ii = report.interval_max if report.interval_max is not None else "?"
        r = report.resources
        u = report.utilization
        lines = [
            f"{prefix}Latency(worst)={lat} cycles  II={ii}",
            f"{prefix}Resources: LUT={r['LUT']}({u['LUT']:.0f}%) "
            f"FF={r['FF']}({u['FF']:.0f}%) "
            f"DSP={r['DSP']}({u['DSP']:.0f}%) "
            f"BRAM={r['BRAM_18K']}({u['BRAM_18K']:.0f}%)"
            f"URAM={r['URAM']}({u['URAM']:.0f}%)",
        ]
        return "\n".join(lines)

    def _hardware_strategies(self, report: SynthReport) -> str:
        """Targeted optimization strategies from the 3-layer hardware KB.

        Combines the dynamic bottleneck analysis (architecture layer) with
        arithmetic (CSD/MCM/CSE) and bitwidth strategies that are triggered by
        the current resource profile.
        """
        strategies = []

        # Architecture layer: dynamic bottleneck analysis.
        _, arch = self._analyze_bottleneck(report)
        strategies.append(arch)

        # Resource profile (absolute counts — U55C has huge capacity, so
        # utilization% alone is a poor signal).
        dsp = report.resources.get("DSP", 0) or 0
        lut = report.resources.get("LUT", 0) or 0
        bram = report.resources.get("BRAM_18K", 0) or 0

        # Arithmetic layer: constant multiplications are likely when DSPs are used.
        if dsp >= 8:
            strategies.append(_CSD_MCM_CSE_GUIDANCE)

        # Bitwidth layer: useful when the resource footprint is non-trivial or
        # the clock is tight.
        if dsp >= 4 or lut >= 500 or bram >= 4:
            strategies.append(_BITWIDTH_GUIDANCE)
        elif report.clock_period_ns is not None and report.clock_period_ns > 8.0:
            strategies.append(_BITWIDTH_GUIDANCE)

        return "\n\n".join(strategies)

    def _opt_feedback(self, best_latency: int | None, baseline_report: SynthReport | None,
                      current_report: SynthReport | None, opt_history: list) -> str:
        """Build a rich optimization feedback string with bottleneck analysis."""
        parts = ["Current design passes correctness and synthesizes."]

        if current_report:
            parts.append(self._ppa_report_text(current_report, prefix="Current: "))
            bottleneck_tag, _ = self._analyze_bottleneck(current_report)
            parts.append(f"Bottleneck: {bottleneck_tag}")
            parts.append(f"Optimization strategy: {self._hardware_strategies(current_report)}")

        if baseline_report and current_report:
            parts.append(
                self._ppa_report_text(baseline_report, prefix="Baseline: ")
            )

        if best_latency:
            parts.append(f"Best latency achieved so far: {best_latency} cycles")

        if opt_history:
            hist_lines = ["Previous optimization attempts:"]
            for i, rpt in enumerate(opt_history):
                status = "(accepted)" if rpt.get("accepted") else "(discarded)"
                lat = rpt.get("latency", "?")
                hist_lines.append(f"  Round {i+1}: latency={lat} {status}")
            parts.append("\n".join(hist_lines))

        return "\n\n".join(parts)

    def _meets_fmax(self, report: SynthReport | None) -> bool:
        """Hard gate: candidate must achieve >= config.FMAX_MIN_MHZ."""
        if report is None or report.clock_period_ns is None:
            return True  # unknown timing -> don't block on it
        return (1000.0 / report.clock_period_ns) >= config.FMAX_MIN_MHZ

    def _compute_qhw(
        self,
        base_report: SynthReport | None,
        cand_report: SynthReport | None,
        mode: str = "balanced",
    ) -> float:
        """Hardware quality metric trading performance vs resources vs timing.

        Matches the reference teams' insight that "faster is not always better":
        a candidate that cuts latency 3.1x but blows up LUT 12x / FF 22x and
        degrades the clock should NOT be promoted. QHW is in [0, 1].
        """
        if cand_report is None:
            return 0.0

        wp, wa, wt = _QHW_WEIGHTS.get(mode, _QHW_WEIGHTS["balanced"])

        # --- performance: latency acceleration vs the baseline, capped ---
        base_lat = _report_lat(base_report) or _report_lat(cand_report) or 1
        cand_lat = _report_lat(cand_report) or base_lat
        if base_lat > 0 and cand_lat > 0:
            accel = base_lat / cand_lat
        else:
            accel = 1.0
        perf = min(accel, 8.0) / 8.0

        # --- area: penalize resource utilization increases over baseline ---
        area = 1.0
        if base_report is not None:
            penalty = 0.0
            for key, wgt in (("LUT", 1.0), ("FF", 1.0), ("DSP", 2.0), ("BRAM_18K", 2.0)):
                base_u = base_report.utilization.get(key, 0) or 0
                cand_u = cand_report.utilization.get(key, 0) or 0
                if cand_u > base_u:
                    penalty += wgt * (cand_u - base_u) / 100.0
            area = 1.0 / (1.0 + penalty)

        # --- timing: reward designs that meet/exceed the Fmax gate ---
        fmax = _report_fmax(cand_report)
        if fmax is None:
            timing = 0.5  # unknown -> neutral
        elif fmax >= config.FMAX_MIN_MHZ:
            timing = min(1.0, fmax / (config.FMAX_MIN_MHZ * 2.0))
        else:
            timing = fmax / config.FMAX_MIN_MHZ

        return wp * perf + wa * area + wt * timing

    def _preflight(self, code: str) -> str | None:
        if not code or len(code.strip()) < 20:
            return "empty or suspiciously short code"
        if "```" in code:
            return "markdown fence leaked into the code"
        if self.task.top not in code:
            return f"top-level function '{self.task.top}' is missing"
        if code.count("{") != code.count("}"):
            return "unbalanced braces (truncated output?)"
        return None

    def _ask(
        self,
        instruction: str,
        code: str,
        feedback: str,
        temperature: float | None = None,
    ) -> str | None:
        parts = [
            f"## Kernel specification\n{self.task.description}",
            f"## Fixed header(s) (read-only)\n```cpp\n{self._header_text()}\n```",
        ]
        hist = self._build_history_text()
        if hist:
            parts.append(hist)
        parts.append(
            f"## Current kernel: {self.task.kernel_name}\n```cpp\n{code}\n```"
        )
        parts.append(f"## Latest tool feedback\n{feedback}")
        parts.append(f"## Your task\n{instruction}")
        user = "\n\n".join(parts)
        resp = self.llm.complete(
            self._system_prompt(), user, temperature=temperature
        )
        return _extract_code(resp)

    def _ask_valid(
        self,
        instruction: str,
        code: str,
        feedback: str,
        temperature: float,
    ) -> str | None:
        extra = ""
        for i in range(self.max_ask_retries):
            cand = self._ask(instruction + extra, code, feedback, temperature)
            if cand is None:
                extra = (
                    "\nYour previous reply contained no usable code block. "
                    "Return the FULL kernel .cpp inside one ```cpp fence."
                )
                continue
            bad = self._preflight(cand)
            if bad is None:
                return cand
            self._log(f"preflight rejected reply ({bad}); re-asking")
            extra = (
                f"\nYour previous reply was rejected: {bad}. "
                "Return the FULL corrected kernel .cpp inside one ```cpp fence, "
                "keeping the exact top-level function signature from the header."
            )
        return None

    # -- error analysis ----------------------------------------------------

    def _describe_failure(self, r: ToolResult) -> str:
        """Build a structured, multi-faceted description of what went wrong."""
        lines = [f"Tool result: {r.brief()}"]
        err_type = _classify_error(r)
        lines.append(f"Error category: {err_type}")
        desc = _ERR_TYPE_DESC.get(err_type)
        if desc:
            lines.append(f"What this means: {desc}")

        # Parse testbench output for structured failure info.
        if r.kind == "csim" and r.phase == "runtime_fail":
            parsed = _parse_testbench_output(r.log)
            if parsed:
                lines.append(f"Testbench analysis: {parsed}")

        # Extract key error lines.
        log_lines = r.log.splitlines()
        hot = [ln for ln in log_lines if any(h in ln for h in _ERR_HINTS)]
        hot_s = "\n".join(hot[-30:]) or "(no explicit error lines found)"
        lines.append(f"--- key error/diagnostic lines ---\n{hot_s}")

        tail = "\n".join(log_lines[-40:])
        lines.append(f"--- log tail ---\n{tail}")

        return "\n".join(lines)

    # -- phases ------------------------------------------------------------

    def _verify_correctness(self, code: str) -> tuple[bool, ToolResult]:
        r = self.server.csim(code)
        if not r.ok:
            return False, r
        if self.task.requires_cosim and self._afford("cosim"):
            cr = self.server.cosim(code)
            return cr.ok, cr
        return True, r

    def _reach_correctness(self, code: str, max_rounds: int | None = None) -> tuple[bool, str]:
        cap = max_rounds if max_rounds is not None else self.repair_rounds
        ok, r = self._verify_correctness(code)
        self._log(f"initial check: {r.brief()}")
        if ok:
            self._log("starting code already correct")
            return True, code

        for attempt in range(1, cap + 1):
            if not self._afford("csim"):
                self._log("out of budget before correctness reached")
                return False, code

            # Classify the error for targeted instruction.
            err_type = _classify_error(r)
            err_desc = _ERR_TYPE_DESC.get(err_type, "")

            instr = (
                f"The design is INCORRECT ({err_type}). {err_desc}\n\n"
                "First, analyze the root cause of this specific failure. "
                "Then, write the FULL corrected kernel inside a ```cpp block."
            )

            new_code = self._ask_valid(
                instr, code, self._describe_failure(r), self.repair_temperature
            )

            if new_code is None:
                self._log(f"repair attempt {attempt}: no valid reply; retrying")
                continue
            if new_code.strip() == code.strip():
                self._log(f"repair attempt {attempt}: LLM returned identical code")
                continue

            code = new_code
            ok, r = self._verify_correctness(code)
            self._log(f"repair attempt {attempt}: {r.brief()}")

            # Record this attempt in history.
            self._history.append(_AttemptRecord(
                attempt=attempt,
                code=code,
                error_type=err_type,
                error_summary=_parse_testbench_output(r.log) or r.phase,
            ))
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[-MAX_HISTORY:]

            if ok:
                gate = "csim+cosim" if self.task.requires_cosim else "csim"
                self._log(f"correctness reached ({gate})")
                return True, code

        return False, code

    def _optimize(self, best: str, best_latency: int | None,
                  max_rounds: int | None = None,
                  baseline_report: SynthReport | None = None) -> str:
        cap = max_rounds if max_rounds is not None else self.opt_rounds
        rounds = 0
        stalls = 0
        stall_threshold = 1 if cap <= 3 else 2
        opt_history: list[dict] = []
        # Track the report associated with the current best design for bottleneck analysis.
        current_report: SynthReport | None = baseline_report

        while (
            rounds < cap
            and stalls < stall_threshold
            and self._afford_round(["csim", "synth"])
        ):
            rounds += 1

            fb = self._opt_feedback(
                best_latency, baseline_report, current_report, opt_history
            )
            instr = (
                "Analyze the current kernel's loop structure and data dependencies. "
                "Identify the bottleneck limiting latency (use the PPA report above), "
                "then apply targeted HLS optimization pragmas to address it. "
                "Return the full optimized kernel inside a ```cpp block."
            )
            cand = self._ask_valid(instr, best, fb, self.opt_temperature)
            if cand is None or cand.strip() == best.strip():
                self._log("no further optimization proposed; stopping")
                break

            cr = self.server.csim(cand)
            if not cr.ok:
                self._log(
                    f"opt round {rounds}: broke correctness ({cr.phase}); discard"
                )
                stalls += 1
                opt_history.append({"latency": None, "accepted": False})
                continue

            sr = self.server.synth(cand)
            if not sr.ok:
                self._log(f"opt round {rounds}: failed synth ({sr.phase}); discard")
                stalls += 1
                opt_history.append({"latency": None, "accepted": False})
                continue

            lat = _lat(sr)
            accepted = False

            # Hard timing gate: reject candidates that fail to reach the
            # minimum Fmax (100 MHz by default, submission requirement).
            if not self._meets_fmax(sr.report):
                fmax = _report_fmax(sr.report)
                self._log(
                    f"opt round {rounds}: latency {best_latency}->{lat} but "
                    f"Fmax {fmax if fmax else '?'}MHz < "
                    f"{config.FMAX_MIN_MHZ:.0f}MHz; rejected (timing gate)"
                )
                stalls += 1
            elif current_report is not None:
                # QHW comparison: promote only if the candidate's hardware
                # quality strictly beats the current best (perf vs resources).
                base_qhw = self._compute_qhw(baseline_report, current_report)
                cand_qhw = self._compute_qhw(baseline_report, sr.report)
                if cand_qhw > base_qhw:
                    accepted = True
                    self._log(
                        f"opt round {rounds}: latency {best_latency}->{lat} "
                        f"QHW {base_qhw:.4f}->{cand_qhw:.4f} ({_report_fmax(sr.report):.0f}MHz); accept"
                    )
                else:
                    self._log(
                        f"opt round {rounds}: latency {best_latency}->{lat} "
                        f"but QHW {base_qhw:.4f}->{cand_qhw:.4f}; rejected (not better hardware)"
                    )
                    stalls += 1
            elif best_latency is None or (lat is not None and lat < best_latency):
                # No current report: fall back to pure latency comparison.
                accepted = True
                self._log(
                    f"opt round {rounds}: latency {best_latency} -> {lat}; accept"
                )
            else:
                self._log(
                    f"opt round {rounds}: no improvement "
                    f"({best_latency} -> {lat})"
                )
                stalls += 1

            if accepted:
                best, best_latency, current_report = cand, lat, sr.report

            opt_history.append({"latency": lat, "accepted": accepted})

        return best

    # -- main --------------------------------------------------------------

    def run(self) -> str:
        t = self.task
        self._log(
            f"task={t.id} type={t.type} difficulty={t.difficulty} "
            f"budget={self.server.budget.total} credits"
        )
        self._log(f"target: {t.part} @ {t.clock_ns} ns")

        # Budget-aware phase allocation: compute round limits based on type + budget.
        auto_repair, auto_opt = self._compute_phase_budgets()
        if auto_repair < self.repair_rounds or auto_opt < self.opt_rounds:
            self._log(
                f"budget-aware rounds: repair {self.repair_rounds}->{auto_repair}, "
                f"opt {self.opt_rounds}->{auto_opt}"
            )

        best = t.kernel_code
        try:
            correct, code = self._reach_correctness(best, auto_repair)
            if not correct:
                self._log("FAILED to reach correctness; returning last attempt")
                return code

            best = code
            verified = best
            best_latency = None
            baseline_report = None
            if self._afford("synth"):
                r = self.server.synth(best)
                if r.ok:
                    best_latency = _lat(r)
                    baseline_report = r.report
                    self._log(
                        f"baseline synth of correct design: {r.report.summary()}"
                    )

            if auto_opt > 0 and self._afford_round(["csim", "synth"]):
                best = self._optimize(best, best_latency, auto_opt, baseline_report)
            elif auto_opt > 0:
                self._log("insufficient budget for any optimization round; skipping")

            if (
                t.requires_cosim
                and best.strip() != verified.strip()
                and self._afford("cosim")
            ):
                cr = self.server.cosim(best)
                self._log(f"post-optimization RTL re-check: {cr.brief()}")
                if not cr.ok:
                    self._log(
                        "optimization reintroduced a structural hazard; reverting"
                    )
                    best = verified

        except BudgetExceeded as e:
            self._log(f"budget exhausted: {e}")

        self._log("done")
        return best
