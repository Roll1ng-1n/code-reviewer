"""评审图共享 state（TypedDict，官方首选；research/langgraph-orchestration §3）。

写入权约定（并行安全的关键）：

- ``diff`` / ``mode`` / ``description``：入口只读
- ``enabled_experts``：仅 router 写（覆盖）
- ``expert_findings``：仅专家节点写，``operator.add`` reducer —— 并行 fan-in 的关键；
  忘写 reducer 时默认覆盖语义会让最后完成的专家静默清掉其他专家的 findings
- ``findings``：仅 aggregate 写（整体替换，单 superstep 单写者）——官方建议的
  「用带排序信息的独立字段落位」写法，聚合输出顺序确定
- ``summary``：仅 verdict 写（覆盖）
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ReviewState(TypedDict):
    diff: str
    mode: str
    description: str
    enabled_experts: list[str]
    expert_findings: Annotated[list[dict[str, Any]], operator.add]
    findings: list[dict[str, Any]]
    summary: dict[str, Any]
