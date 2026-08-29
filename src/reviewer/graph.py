"""评审图 —— Spec #12 拓扑的曳光弹最小版::

    START → context_assembly（#17：结构地图 + import 邻域，纯本地零模型）
    → router（确定性，固定启用 logic）—[Send]→ expert_logic → aggregate → verdict → END

后续票扩展点：

- #18 四专家 + 动态 fan-out（``enabled_experts`` 决定 Send 列表）
- #20 aggregator 完整版（去重合并 + 复核过滤）
- #22 单 Agent 基线（共享 provider/state/schema 的对照图）
- #23 ``--interactive`` interrupt()
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .aggregate import aggregate
from .context import assemble_context
from .experts.logic import make_logic_expert
from .model import ModelProvider
from .state import ReviewState
from .verdict import verdict_node

LOGIC_EXPERT = "expert_logic"


def _router(state: ReviewState) -> dict:
    """确定性路由（零 LLM，可调试可复现）：曳光弹阶段固定只启用 logic 专家。"""
    return {"enabled_experts": ["logic"]}


def _assemble_context(state: ReviewState) -> dict:
    """context-assembly 节点（#17）：diff 读入之后、专家之前的上下文组装。

    纯本地计算（os.walk + stdlib ast，零模型调用）：结构地图 / import 邻域 /
    预算统计写入共享 state（写入权约定见 state.py docstring）。repo_root 缺失
    （纯回放无快照）时两块上下文为空、计数 0，不致命。
    """
    return assemble_context(
        diff=state["diff"],
        repo_root=state.get("repo_root") or None,
        description=state.get("description", ""),
    )


def _route_sends(state: ReviewState) -> list[Send]:
    """条件边：按 ``enabled_experts`` 运行时构造 Send 列表（分支数由数据决定）。

    Send arg 即该专家任务的全部输入（可与核心 state 不同形）。
    """
    inputs = {
        "diff": state["diff"],
        "mode": state["mode"],
        "description": state.get("description", ""),
        # #17：组装产物随 Send 下发——专家拿到的不再是裸 diff
        "structure_map": state.get("structure_map", ""),
        "neighborhood": state.get("neighborhood", ""),
    }
    sends: list[Send] = []
    if "logic" in state["enabled_experts"]:
        sends.append(Send(LOGIC_EXPERT, inputs))
    return sends


def build_review_graph(provider: ModelProvider):
    """编译评审图。provider 经闭包注入——测试传假 provider 即得零网络缝。"""
    builder = StateGraph(ReviewState)
    builder.add_node("context_assembly", _assemble_context)
    builder.add_node("router", _router)
    builder.add_node(LOGIC_EXPERT, make_logic_expert(provider))
    builder.add_node("aggregate", aggregate)
    builder.add_node("verdict", verdict_node)

    builder.add_edge(START, "context_assembly")
    builder.add_edge("context_assembly", "router")
    builder.add_conditional_edges("router", _route_sends, [LOGIC_EXPERT])
    builder.add_edge(LOGIC_EXPERT, "aggregate")
    builder.add_edge("aggregate", "verdict")
    builder.add_edge("verdict", END)
    return builder.compile()
