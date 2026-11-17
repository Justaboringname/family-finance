"""每个税额旁边的「规则 + 参数 + 出处」明细。"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .params import ParamRef


@dataclass
class TraceLine:
    label: str
    amount: Decimal | None = None
    rule: str = ""
    params: list[ParamRef] = field(default_factory=list)
    kind: str = "info"  # info / tax / income / warning


class Trace:
    def __init__(self) -> None:
        self.lines: list[TraceLine] = []

    def add(self, label, amount=None, rule="", params=None, kind="info") -> None:
        self.lines.append(TraceLine(label, amount, rule, list(params or []), kind))

    def warn(self, label, rule="", params=None) -> None:
        self.add(label, None, rule, params, "warning")

    def extend(self, other: "Trace", prefix: str = "") -> None:
        for ln in other.lines:
            self.lines.append(TraceLine(prefix + ln.label, ln.amount, ln.rule, ln.params, ln.kind))

    @property
    def warnings(self) -> list[TraceLine]:
        return [ln for ln in self.lines if ln.kind == "warning"]
