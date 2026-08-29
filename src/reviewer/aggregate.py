"""Aggregator（曳光弹版 = 透传）：确定性排序 + 统一编号。

完整版（确定性预分组 → LLM 仅组内合并、拿不准不合并、severity 只降不升、
逐条复核过滤）由 实现 8/11（#20）接管；本版只保证管道通与确定性输出。
"""

from __future__ import annotations

from .contract import number_findings, sort_findings


def aggregate(state: dict) -> dict:
    """fan-in 聚合节点：唯一写 ``findings`` 的节点（整体替换安全）。"""
    return {"findings": number_findings(sort_findings(state.get("expert_findings", [])))}
