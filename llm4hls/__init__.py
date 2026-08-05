"""LLM4HLS Track A: a minimal budgeted HLS agent + evaluation harness.

A reference implementation of the Track A workflow, end to end:
  task package -> metered csim/synth/cosim tools under a credit budget ->
  correctness-before-PPA agent loop -> hidden-test grading + PPA scorecard.
"""

from .agent import ReferenceAgent
from .budget import Budget, BudgetExceeded
from .harness import ToolServer
from .scoring import Scorecard, grade
from .task import Task, load_task
from .tools import CoSimTool, CSimTool, SynthTool, ToolResult

__all__ = [
    "ReferenceAgent",
    "Budget",
    "BudgetExceeded",
    "ToolServer",
    "Scorecard",
    "grade",
    "Task",
    "load_task",
    "CSimTool",
    "SynthTool",
    "CoSimTool",
    "ToolResult",
]
