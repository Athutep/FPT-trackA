"""Low-level `vitis-run --mode hls --tcl <tcl>` runner — Windows/Linux.

Sources the pinned Vitis settings script, then runs a generated TCL script in a
given working directory. On Windows the command is written to a temporary .bat
file and executed via `cmd /c <file>` (avoids cmd's nested-quote parsing bugs
with subprocess's list2cmdline); on Linux uses `bash -c` + settings64.sh.
Vitis 2025.2 replaced the standalone `vitis_hls` binary with
`vitis-run --mode hls --tcl <script>`; the HLS Tcl commands
(open_project / csynth_design / cosim_design / ...) are unchanged.

On timeout the entire process tree is killed (Vitis spawns children), so
nothing is left hanging. Stdlib-only (no psutil).
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

_IS_WINDOWS = platform.system() == "Windows"


@dataclass
class ProcResult:
    return_code: int
    stdout: str
    stderr: str
    elapsed_s: float
    timeout: bool


# ── public API ──────────────────────────────────────────────────────────────

def run_vitis_tcl(tcl_text: str, workdir: Path, timeout_s: float) -> ProcResult:
    """Write `tcl_text` to workdir/run_hls.tcl and run vitis-run on it."""
    workdir.mkdir(parents=True, exist_ok=True)
    tcl_fp = workdir / "run_hls.tcl"
    tcl_fp.write_text(tcl_text, encoding="utf-8")

    if _IS_WINDOWS:
        # Batch file avoids cmd /c nested-quote issues with spaces in paths.
        inner = (
            f'@echo off\r\n'
            f'call "{config.VITIS_SETTINGS}" >nul 2>&1\r\n'
            f'vitis-run --mode hls --tcl run_hls.tcl\r\n'
            f'exit /b %errorlevel%\r\n'
        )
    else:
        inner = (
            f"source '{config.VITIS_SETTINGS}' >/dev/null 2>&1 "
            "&& exec vitis-run --mode hls --tcl run_hls.tcl"
        )
    return _run_shell(inner, workdir, timeout_s)


def run_binary(binary: Path, workdir: Path, timeout_s: float) -> ProcResult:
    """Run a compiled executable (e.g. csim.exe) and capture its return code.

    On Windows the Vitis environment is sourced first (so the csim runtime
    DLLs are on PATH); on Linux the binary is wrapped in exec.
    """
    if _IS_WINDOWS:
        inner = (
            f'@echo off\r\n'
            f'call "{config.VITIS_SETTINGS}" >nul 2>&1\r\n'
            f'"{binary}"\r\n'
            f'exit /b %errorlevel%\r\n'
        )
        return _run_shell(inner, workdir, timeout_s)
    else:
        return _run_shell(f"exec '{binary}'", workdir, timeout_s)


# ── internal helpers ────────────────────────────────────────────────────────

def _run_direct(args: list[str], workdir: Path, timeout_s: float) -> ProcResult:
    """Spawn an executable directly (no shell wrapper)."""
    t0 = time.monotonic()
    p = subprocess.Popen(
        args,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process(p)
        stdout, stderr = p.communicate()
        return ProcResult(-1, stdout or "", stderr or "", time.monotonic() - t0, True)
    return ProcResult(p.returncode, stdout or "", stderr or "", time.monotonic() - t0, False)


def _run_shell(inner_cmd: str, workdir: Path, timeout_s: float) -> ProcResult:
    """Run a command via the OS shell.

    On Windows `inner_cmd` is treated as the body of a batch file; it is
    written to workdir/_run.bat and executed via `cmd /c <file>`. On Linux it
    is run via `bash -c`.
    """
    t0 = time.monotonic()
    if _IS_WINDOWS:
        bat = workdir / "_run.bat"
        bat.write_text(inner_cmd, encoding="ascii")
        p = subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    else:
        p = subprocess.Popen(
            ["bash", "-c", inner_cmd],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    try:
        stdout, stderr = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process(p)
        stdout, stderr = p.communicate()
        return ProcResult(-1, stdout or "", stderr or "", time.monotonic() - t0, True)
    return ProcResult(p.returncode, stdout or "", stderr or "", time.monotonic() - t0, False)


def _kill_process(p: subprocess.Popen) -> None:
    """Kill a process tree. Uses taskkill /T on Windows, SIGKILL on Linux."""
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    else:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
