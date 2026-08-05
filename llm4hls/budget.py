"""Credit-based tool-invocation budget.

Every metered tool call (csim / synth / cosim) costs credits weighted by its
typical wall-clock cost. The agent must reach correctness and optimize PPA
before the credit pool runs dry. A charge that would overrun raises
BudgetExceeded, which the agent loop treats as a hard stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetCall:
    kind: str
    cost: int
    spent_after: int


@dataclass
class Budget:
    total: int
    cost: dict = field(default_factory=lambda: dict(config.CREDIT_COST))
    spent: int = 0
    calls: list[BudgetCall] = field(default_factory=list)

    def remaining(self) -> int:
        return self.total - self.spent

    def can_afford(self, kind: str) -> bool:
        return self.remaining() >= self.cost[kind]

    def can_afford_round(self, kinds: list[str]) -> bool:
        """Check whether all given tool kinds can be afforded together."""
        total = sum(self.cost[k] for k in kinds)
        return self.remaining() >= total

    def charge(self, kind: str) -> None:
        c = self.cost[kind]
        if self.remaining() < c:
            raise BudgetExceeded(
                f"{kind} costs {c} but only {self.remaining()}/{self.total} credits left"
            )
        self.spent += c
        self.calls.append(BudgetCall(kind, c, self.spent))

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for c in self.calls:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        breakdown = ", ".join(f"{k}x{v}" for k, v in counts.items()) or "none"
        return f"budget {self.spent}/{self.total} credits spent ({breakdown})"
