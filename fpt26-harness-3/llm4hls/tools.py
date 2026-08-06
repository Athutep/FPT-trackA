"""Vitis HLS tool wrappers: C-simulation, C-synthesis, and C/RTL co-simulation.

These are the raw tools. The metered, budget-charged interface the agent sees
lives in harness.py; grading calls these directly (uncharged, server-side).

Design targets (part/clock) are parameters, defaulting to the pinned U55C.

C-simulation is split into two steps: `csim_design -setup` only compiles, so a
non-zero return there is unambiguously a *compile* error; the produced csim.exe
is then run separately, so its exit code is unambiguously a *functional*
pass/fail. That separation is what lets the agent diagnose the failure mode.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config, vitis
from .report import CoSimResult, SynthReport, parse_cosim_rpt, parse_csynth_xml


def _truncate(text: str, head: int = 6000, tail: int = 6000) -> str:
    if len(text) <= head + tail:
        return text
    return (
        text[:head]
        + f"\n... [{len(text) - head - tail} chars elided] ...\n"
        + text[-tail:]
    )


@dataclass
class ToolResult:
    kind: str  # "csim" | "synth" | "cosim"
    ok: bool
    phase: (
        str  # pass | compile_error | runtime_fail | synth_error | cosim_fail | timeout
    )
    return_code: int
    log: str
    elapsed_s: float
    report: SynthReport | None = None
    cosim: CoSimResult | None = None

    def brief(self) -> str:
        s = f"[{self.kind}] {self.phase} (rc={self.return_code}, {self.elapsed_s:.1f}s)"
        if self.report is not None:
            s += " | " + self.report.summary()
        if self.cosim is not None:
            s += " | " + self.cosim.summary()
        return s


def _write_files(dest: Path, files: dict[str, str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        # UTF-8: LLM-generated kernels can contain unicode (e.g. ‑) that the
        # Windows default (gbk) encoding cannot write.
        (dest / name).write_text(content, encoding="utf-8")


class CSimTool:
    """Compile the testbench + kernel and run it; return code 0 == correct."""

    def run(
        self,
        build_dir: Path,
        files: dict[str, str],
        top: str,
        part: str = config.DEFAULT_PART,
        clock_ns: float = config.DEFAULT_CLOCK_NS,
        data_files: dict[str, bytes] | None = None,
    ) -> ToolResult:
        work = build_dir
        if work.exists():
            shutil.rmtree(work)
        _write_files(work, files)
        if data_files:
            for name, blob in data_files.items():
                (work / name).write_bytes(blob)

        cpp_files = [n for n in files if n.endswith((".cpp", ".cc", ".c"))]
        tcl = "open_project csim_proj\n"
        for f in cpp_files:
            tcl += f"add_files -tb {f}\n"
        tcl += f"open_solution sol -flow_target {config.DEFAULT_FLOW_TARGET}\n"
        tcl += f"set_top {top}\n"
        tcl += f"set_part {part}\n"
        tcl += f"create_clock -period {clock_ns} -name clk_default\n"
        tcl += "csim_design -setup\n"
        tcl += "exit\n"

        r = vitis.run_vitis_tcl(tcl, work, config.CSIM_TIMEOUT_S)
        log = _truncate(r.stdout + "\n" + r.stderr)
        if r.timeout:
            return ToolResult("csim", False, "timeout", -1, log, r.elapsed_s)
        if r.return_code != 0:
            return ToolResult(
                "csim", False, "compile_error", r.return_code, log, r.elapsed_s
            )

        csim_exe = work / "csim_proj" / "sol" / "csim" / "build" / "csim.exe"
        if not csim_exe.exists():
            return ToolResult(
                "csim", False, "compile_error", r.return_code, log, r.elapsed_s
            )

        # Run the executable in its own build dir; stage any data files there too.
        exe_dir = csim_exe.parent
        if data_files:
            for name, blob in data_files.items():
                (exe_dir / name).write_bytes(blob)
        rr = vitis.run_binary(csim_exe, exe_dir, config.CSIM_TIMEOUT_S)
        run_log = _truncate(rr.stdout + "\n" + rr.stderr)
        if rr.timeout:
            return ToolResult(
                "csim", False, "timeout", -1, run_log, r.elapsed_s + rr.elapsed_s
            )
        ok = rr.return_code == 0
        return ToolResult(
            "csim",
            ok,
            "pass" if ok else "runtime_fail",
            rr.return_code,
            run_log,
            r.elapsed_s + rr.elapsed_s,
        )


class SynthTool:
    """Run C-synthesis; on success attach the parsed csynth.xml report."""

    def run(
        self,
        build_dir: Path,
        files: dict[str, str],
        synth_sources: list[str],
        top: str,
        part: str = config.DEFAULT_PART,
        clock_ns: float = config.DEFAULT_CLOCK_NS,
    ) -> ToolResult:
        work = build_dir
        if work.exists():
            shutil.rmtree(work)
        _write_files(work, files)

        tcl = "open_project synth_proj\n"
        for f in synth_sources:
            tcl += f"add_files {f}\n"
        tcl += f"open_solution sol -flow_target {config.DEFAULT_FLOW_TARGET}\n"
        tcl += f"set_top {top}\n"
        tcl += f"set_part {part}\n"
        tcl += f"create_clock -period {clock_ns} -name clk_default\n"
        tcl += "config_compile -unsafe_math_optimizations\n"
        tcl += "csynth_design\n"
        tcl += "exit\n"

        r = vitis.run_vitis_tcl(tcl, work, config.SYNTH_TIMEOUT_S)
        log = _truncate(r.stdout + "\n" + r.stderr)
        if r.timeout:
            return ToolResult("synth", False, "timeout", -1, log, r.elapsed_s)
        if r.return_code != 0:
            return ToolResult(
                "synth", False, "synth_error", r.return_code, log, r.elapsed_s
            )

        xml_fp = work / "synth_proj" / "sol" / "syn" / "report" / "csynth.xml"
        report = parse_csynth_xml(xml_fp) if xml_fp.exists() else None
        return ToolResult("synth", True, "pass", 0, log, r.elapsed_s, report=report)


class CoSimTool:
    """C/RTL co-simulation: csynth then cosim_design.

    This is where structural bugs that C-simulation cannot see surface: a
    deadlock hangs the RTL sim (-> timeout), and an RTL/C mismatch or invalid
    streaming behaviour reports FAIL. On success the report carries the
    *measured* (not estimated) latency.
    """

    def run(
        self,
        build_dir: Path,
        files: dict[str, str],
        synth_sources: list[str],
        tb_sources: list[str],
        top: str,
        part: str = config.DEFAULT_PART,
        clock_ns: float = config.DEFAULT_CLOCK_NS,
    ) -> ToolResult:
        work = build_dir
        if work.exists():
            shutil.rmtree(work)
        _write_files(work, files)

        tcl = "open_project cosim_proj\n"
        for f in synth_sources:
            tcl += f"add_files {f}\n"
        for f in tb_sources:
            tcl += f"add_files -tb {f}\n"
        tcl += f"open_solution sol -flow_target {config.DEFAULT_FLOW_TARGET}\n"
        tcl += f"set_top {top}\n"
        tcl += f"set_part {part}\n"
        tcl += f"create_clock -period {clock_ns} -name clk_default\n"
        tcl += "csynth_design\n"
        tcl += "cosim_design\n"
        tcl += "exit\n"

        r = vitis.run_vitis_tcl(tcl, work, config.COSIM_TIMEOUT_S)
        log = _truncate(r.stdout + "\n" + r.stderr)
        if r.timeout:
            # A hung RTL sim is the classic deadlock signature.
            return ToolResult("cosim", False, "timeout", -1, log, r.elapsed_s)

        sol = work / "cosim_proj" / "sol"
        synth_ok = (sol / "syn" / "report" / "csynth.xml").exists()
        rpt_fp = sol / "sim" / "report" / f"{top}_cosim.rpt"
        cosim = parse_cosim_rpt(rpt_fp) if rpt_fp.exists() else None

        if not synth_ok:
            return ToolResult(
                "cosim", False, "synth_error", r.return_code, log, r.elapsed_s
            )
        if cosim is None or not cosim.passed:
            return ToolResult(
                "cosim",
                False,
                "cosim_fail",
                r.return_code,
                log,
                r.elapsed_s,
                cosim=cosim,
            )
        return ToolResult(
            "cosim", True, "pass", r.return_code, log, r.elapsed_s, cosim=cosim
        )
