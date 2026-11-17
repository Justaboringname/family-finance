"""读取 ./tax_params/<年份>.json。

约定：含 "value" 键的 dict 是一个参数，必须带 "source"（http 链接）；
不含 "value" 的 dict 是分组；"_meta" 是说明。代码里不写死任何税率。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .money import D

PARAMS_DIR = Path(__file__).resolve().parent.parent / "tax_params"


@dataclass(frozen=True)
class ParamRef:
    path: str
    value: object
    source: str
    note: str = ""

    def short(self) -> str:
        return f"{self.path} = {self.value}"


class ParamError(Exception):
    pass


class TaxParams:
    def __init__(self, year: int, data: dict):
        self.year = year
        self.data = data
        meta = data.get("_meta", {})
        self.status = meta.get("status", "unknown")
        self.note = meta.get("note", "")

    @property
    def provisional(self) -> bool:
        return self.status != "official"

    def ref(self, path: str) -> ParamRef:
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ParamError(f"{self.year} 年参数缺少 {path}")
            node = node[part]
        if not isinstance(node, dict) or "value" not in node:
            raise ParamError(f"{path} 不是参数节点")
        return ParamRef(path, node["value"], node.get("source", ""), node.get("note", ""))

    def v(self, path: str):
        return self.ref(path).value

    def dec(self, path: str):
        return D(self.v(path))

    def brackets(self, path: str) -> list[tuple]:
        """[[上限, 税率, (速算扣除数)], ...] → Decimal 元组；上限 None 表示无穷。"""
        out = []
        for row in self.v(path):
            cap = None if row[0] is None else D(row[0])
            out.append((cap, *[D(x) for x in row[1:]]))
        return out

    def fractions(self, path: str) -> list[Fraction]:
        return [Fraction(x) for x in self.v(path)]


def available_years() -> list[int]:
    return sorted(int(p.stem) for p in PARAMS_DIR.glob("*.json") if p.stem.isdigit())


_cache: dict[int, TaxParams] = {}


def load_params(year: int) -> TaxParams:
    if year not in _cache:
        path = PARAMS_DIR / f"{year}.json"
        if not path.exists():
            raise ParamError(f"没有 {year} 年的参数文件 tax_params/{year}.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        problems = validate(data)
        if problems:
            raise ParamError(f"{path.name} 参数缺来源：" + "；".join(problems))
        _cache[year] = TaxParams(year, data)
    return _cache[year]


def validate(data: dict, prefix: str = "") -> list[str]:
    """返回所有问题：参数缺 source，或数值没有包在参数节点里。"""
    problems = []
    for key, node in data.items():
        if key == "_meta":
            continue
        path = f"{prefix}{key}"
        if isinstance(node, dict) and "value" in node:
            src = node.get("source", "")
            if not (isinstance(src, str) and src.startswith("http")):
                problems.append(f"{path} 缺少 source 链接")
        elif isinstance(node, dict):
            problems.extend(validate(node, path + "."))
        else:
            problems.append(f"{path} 不是 {{value, source}} 参数")
    return problems
