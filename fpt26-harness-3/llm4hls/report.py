"""Parser for Vitis HLS C-synthesis reports (`csynth.xml`).

Captures the Performance (latency, II, clock) and Area (LUT/FF/DSP/BRAM/URAM)
estimates. There is no Power figure at the C-synthesis stage; PPA "P" is
represented by resource usage as a proxy (see scoring.py).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

_RESOURCES = ("LUT", "FF", "DSP", "BRAM_18K", "URAM")


def _to_int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None  # e.g. "undef" for data-dependent latency


@dataclass
class SynthReport:
    clock_period_ns: float | None
    latency_best: int | None
    latency_avg: int | None
    latency_worst: int | None
    interval_min: int | None  # initiation interval (II)
    interval_max: int | None
    resources: dict  # used: {LUT, FF, DSP, BRAM_18K, URAM}
    available: dict  # device totals
    utilization: dict  # used / available, in %

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lat = self.latency_worst if self.latency_worst is not None else "?"
        ii = self.interval_max if self.interval_max is not None else "?"
        r = self.resources
        return (
            f"latency(worst)={lat} cyc  II={ii}  clk~{self.clock_period_ns}ns  "
            f"LUT={r['LUT']} FF={r['FF']} DSP={r['DSP']} BRAM={r['BRAM_18K']} URAM={r['URAM']}"
        )


def parse_csynth_xml(xml_fp: Path) -> SynthReport:
    root = ET.parse(xml_fp).getroot()

    perf = root.find("PerformanceEstimates")
    timing = perf.find("SummaryOfTimingAnalysis") if perf is not None else None
    latency = perf.find("SummaryOfOverallLatency") if perf is not None else None

    clock = None
    if timing is not None:
        clock_txt = timing.findtext("EstimatedClockPeriod")
        clock = float(clock_txt) if clock_txt else None

    def lat(tag: str) -> int | None:
        return _to_int(latency.findtext(tag)) if latency is not None else None

    area = root.find("AreaEstimates")
    used_el = area.find("Resources") if area is not None else None
    avail_el = area.find("AvailableResources") if area is not None else None

    used = {
        k: (_to_int(used_el.findtext(k)) if used_el is not None else None)
        for k in _RESOURCES
    }
    avail = {
        k: (_to_int(avail_el.findtext(k)) if avail_el is not None else None)
        for k in _RESOURCES
    }
    util = {}
    for k in _RESOURCES:
        u, a = used[k], avail[k]
        util[k] = round(100.0 * u / a, 3) if (u is not None and a) else 0.0

    return SynthReport(
        clock_period_ns=clock,
        latency_best=lat("Best-caseLatency"),
        latency_avg=lat("Average-caseLatency"),
        latency_worst=lat("Worst-caseLatency"),
        interval_min=lat("Interval-min"),
        interval_max=lat("Interval-max"),
        resources=used,
        available=avail,
        utilization=util,
    )


@dataclass
class CoSimResult:
    """RTL-verified result parsed from `<top>_cosim.rpt`."""

    status: str  # "Pass" | "Fail" | "NA"
    latency_min: int | None
    latency_avg: int | None
    latency_max: int | None

    @property
    def passed(self) -> bool:
        return self.status.lower() == "pass"

    def summary(self) -> str:
        return f"cosim={self.status} measured_latency(max)={self.latency_max}"


def parse_cosim_rpt(rpt_fp: Path) -> CoSimResult | None:
    """Parse the co-simulation report's RTL results table.

    The table row looks like:
      |   Verilog|      Pass|   min | avg | max | ... (interval) ... | total |
    """
    text = rpt_fp.read_text()
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        # cells[1] is the RTL flavour (VHDL/Verilog); pick whichever ran.
        if len(cells) >= 6 and cells[1] in ("Verilog", "VHDL") and cells[2] != "NA":
            return CoSimResult(
                status=cells[2],
                latency_min=_to_int(cells[3]),
                latency_avg=_to_int(cells[4]),
                latency_max=_to_int(cells[5]),
            )
    return None
