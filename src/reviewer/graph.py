"""评审图 —— Spec #12 拓扑的曳光弹最小版::

    START → router（确定性，固定启用 logic）—[Send]→ expert_logic → aggregate → verdict → END

后续票扩展点：

- #17 context-assembly（结构地图 + import 邻域）插在 router 之前
- #18 四专家 + 动态 fan-out（``enabled_experts`` 决定 Send 列表）
- #20 aggregator 完整版（去重合并 + 复核过滤）
- #22 单 Agent 基线（共享 provider/state/schema 的对照图）
- #23 ``--interactive`` interrupt()
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .aggregate import aggregate
from .experts.logic import make_logic_expert
from .model import ModelProvider
from .state import ReviewState
from .verdict import verdict_node

LOGIC_EXPERT = "expert_logic"


def _router(state: ReviewState) -> dict:
    """确定性路由（零 LLM，可调试可复现）：曳光弹阶段固定只启用 logic 专家。"""
    return {"enabled_experts": ["logic"]}


def _route_sends(state: ReviewState) -> list[Send]:
    """条件边：按 ``enabled_experts`` 运行时构造 Send 列表（分支数由数据决定）。

    Send arg 即该专家任务的全部输入（可与核心 state 不同形）。
    """
    inputs = {
        "diff": state["diff"],
        "mode": state["mode"],
        "description": state.get("description", ""),
    }
    sends: list[Send] = []
    if "logic" in state["enabled_experts"]:
        sends.append(Send(LOGIC_EXPERT, inputs))
    return sends


def build_review_graph(provider: ModelProvider):
    """编译评审图。provider 经闭包注入——测试传假 provider 即得零网络缝。"""
    builder = StateGraph(ReviewState)
    builder.add_node("router", _router)
    builder.add_node(LOGIC_EXPERT, make_logic_expert(provider))
    builder.add_node("aggregate", aggregate)
    builder.add_node("verdict", verdict_node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", _route_sends, [LOGIC_EXPERT])
    builder.add_edge(LOGIC_EXPERT, "aggregate")
    builder.add_edge("aggregate", "verdict")
    builder.add_edge("verdict", END)
    return builder.compile()
