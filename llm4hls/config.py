"""Global toolchain + target constants for the LLM4HLS Track A harness.

All values are overridable via environment variables so the same harness runs
against a different Vitis install or target board without code changes.

Competition targets are pinned here (Vitis 2025.2 + Alveo U55C @ 200 MHz),
mirroring the decisions locked for the LLM4HLS Track A benchmark.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

_IS_WINDOWS = platform.system() == "Windows"

# --- Toolchain -------------------------------------------------------------
# Root of the Vitis install; its settings script is sourced before every
# `vitis-run` invocation so the harness needs nothing on the ambient PATH.
# Vitis 2025.2 dropped the standalone `vitis_hls` binary: HLS now runs via
# `vitis-run --mode hls --tcl <script>` (see vitis.py).

_VITIS_DEFAULT_WIN = r"E:\Xilinx\2025.2\Vitis"
_VITIS_DEFAULT_UNIX = "/tools/Xilinx/Vitis/2025.2"

VITIS_HLS_ROOT = Path(
    os.environ.get(
        "LLM4HLS_VITIS_HLS_ROOT",
        _VITIS_DEFAULT_WIN if _IS_WINDOWS else _VITIS_DEFAULT_UNIX,
    )
)
VITIS_SETTINGS = VITIS_HLS_ROOT / ("settings64.bat" if _IS_WINDOWS else "settings64.sh")

# --- Target constraints (pinned for the competition) -----------------------
DEFAULT_PART = os.environ.get("LLM4HLS_PART", "xcu55c-fsvh2892-2L-e")
DEFAULT_CLOCK_NS = float(os.environ.get("LLM4HLS_CLOCK_NS", "5.0"))
DEFAULT_FLOW_TARGET = "vivado"

# --- Tool timeouts (seconds) ----------------------------------------------
CSIM_TIMEOUT_S = float(os.environ.get("LLM4HLS_CSIM_TIMEOUT_S", "180"))
SYNTH_TIMEOUT_S = float(os.environ.get("LLM4HLS_SYNTH_TIMEOUT_S", "600"))
COSIM_TIMEOUT_S = float(os.environ.get("LLM4HLS_COSIM_TIMEOUT_S", "900"))

# --- Budget: credit cost per tool call ------------------------------------
CREDIT_COST = {
    "csim": int(os.environ.get("LLM4HLS_COST_CSIM", "1")),
    "synth": int(os.environ.get("LLM4HLS_COST_SYNTH", "4")),
    "cosim": int(os.environ.get("LLM4HLS_COST_COSIM", "20")),
}

# --- Hardware quality gate (QHW) -----------------------------------------
# Minimum Fmax (MHz) a candidate must achieve to be promoted. Submission
# requirement is >= 100 MHz; the task target is 200 MHz.
FMAX_MIN_MHZ = float(os.environ.get("LLM4HLS_FMAX_MIN_MHZ", "100"))
# QHW weighting mode: balanced | speed | area. Controls how the agent trades
# performance (latency) against resource cost when promoting candidates.
QHW_MODE = os.environ.get("LLM4HLS_QHW_MODE", "balanced")

# --- Reference agent LLM backend (OpenRouter, open-source models only) -----
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
)
DEFAULT_LLM_MODEL = os.environ.get("LLM4HLS_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
