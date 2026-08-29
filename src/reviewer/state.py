"""评审图共享 state（TypedDict，官方首选；research/langgraph-orchestration §3）。

写入权约定（并行安全的关键）：

- ``diff`` / ``mode`` / ``description`` / ``repo_root``：入口只读
  （``repo_root`` 由 #16 接线：被审仓库根路径，#17 结构地图 / spec KB 相对路径消费）
- ``structure_map`` / ``neighborhood`` / ``context_stats``：仅 context-assembly 写
  （#17 上下文组装产物；context_stats 由 CLI 层并入 metadata）
- ``spec_kb``：仅 spec-kb 节点写（#19 Spec KB 加载产物：documents 文档名列表 /
  rendered_text / stats / hash；router 读 documents 做「KB 空剔除 spec」，spec
  专家经 Send 读 rendered_text）
- ``enabled_experts``：仅 router 写（覆盖；#18 起由 config.experts.enabled 过滤而来）
- ``expert_findings``：仅专家节点写，``operator.add`` reducer —— 并行 fan-in 的关键；
  忘写 reducer 时默认覆盖语义会让最后完成的专家静默清掉其他专家的 findings
  （#18 四专家 Send fan-out 并行 superstep 各写各的增量，只追加不覆盖）
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
    repo_root: str  # 被审仓库根路径（#16 接线，入口只读；#17 消费）
    structure_map: str  # #17：结构地图（目录树 + AGENTS.md 模块职责），仅 context_assembly 写
    neighborhood: str  # #17：import 邻域（被改 .py 的直接依赖源码），仅 context_assembly 写
    context_stats: dict[str, Any]  # #17：上下文预算统计（降级/截断可观测，CLI 并入 metadata）
    spec_kb: dict[str, Any]  # #19：Spec KB 加载产物（documents/rendered_text/stats/hash），仅 spec-kb 节点写
    enabled_experts: list[str]
    expert_findings: Annotated[list[dict[str, Any]], operator.add]
    findings: list[dict[str, Any]]
    summary: dict[str, Any]
