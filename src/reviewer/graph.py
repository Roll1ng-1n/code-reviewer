"""评审图 —— Spec #12 拓扑（实现 6/11 #18 专家团 Send fan-out + 实现 7/11 #19 Spec KB）::

    START → context_assembly（#17：结构地图 + import 邻域，纯本地零模型）
    → spec_kb（#19：三层来源加载，KB 空 = 合法态）→ router（纯规则，零 LLM）
    —[Send × N]→ expert_architecture / expert_logic / expert_spec / expert_style
    （并行 superstep）→ aggregate → verdict → END

- router：``config.experts.enabled`` 过滤出 ``enabled_experts``（确定性、可复现），
  分支数运行时决定——条件边据此返回 Send 列表；KB 空规则（#19 接线）：
  ``spec_kb.documents`` 为空 → 从 enabled 中剔除 spec，「可留空」在图结构上落地。
- spec-kb 节点：``load_spec_kb``（src/reviewer/spec_kb.py）三层来源合并 +
  sha256 去重 + >500 行降级，产物整体写入 ``spec_kb``（单写者，覆盖语义安全）。
- 四专家：单 charter 单 category（charter 移植自 eval/golden-set/compare.py
  的 arm_panel 对应臂），各自返回增量 ``expert_findings``，经 ``operator.add``
  reducer 汇合——并行输出零静默丢失（覆盖语义会清掉其他专家的写入）。
- aggregate：透传 + 确定性排序 + 统一编号（完整版归 #20）。

后续票扩展点：

- #20 aggregator 完整版（预分组 + LLM 组内合并 + 复核过滤）
- #22 单 Agent 基线（共享 provider/state/schema 的对照图）
- #23 ``--interactive`` interrupt()
"""

from __future__ import annotations

from collections.abc import Sequence

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .aggregate import aggregate
from .context import assemble_context
from .experts.architecture import make_architecture_expert
from .experts.logic import make_logic_expert
from .experts.spec import make_spec_expert
from .experts.style import make_style_expert
from .model import ModelProvider
from .spec_kb import SpecSources, load_spec_kb
from .state import ReviewState
from .verdict import verdict_node

# category（contract.Category / config.experts.enabled 取值）→ 图节点名
EXPERT_NODES: dict[str, str] = {
    "architecture": "expert_architecture",
    "logic": "expert_logic",
    "spec": "expert_spec",
    "style": "expert_style",
}

# category → 节点工厂（build 时闭包注入 provider）
_EXPERT_FACTORIES = {
    "architecture": make_architecture_expert,
    "logic": make_logic_expert,
    "spec": make_spec_expert,
    "style": make_style_expert,
}


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


def build_review_graph(
    provider: ModelProvider,
    *,
    experts_enabled: Sequence[str],
    spec_sources: SpecSources | None = None,
):
    """编译评审图。provider 经闭包注入——测试传假 provider 即得零网络缝。

    ``experts_enabled``：router 的纯规则输入（CLI 传 ``config.experts.enabled``），
    决定 fan-out 的候选集合；实际分支数由 router 运行时过滤结果决定。
    ``spec_sources``：#19 三层来源的 CLI/config 侧打包（None = 无来源 → KB 恒空）。
    """

    def _load_spec_kb(state: ReviewState) -> dict:
        """spec-kb 节点（#19）：context-assembly 之后、router 之前加载规范库。

        纯本地（文件读 + sha256，零模型）；repo_root 缺失（纯回放无快照）时
        约定目录不生效，仅 CLI/config 来源按 base_dir 解析。产物整体覆盖写入
        ``spec_kb``（单写者单 superstep，覆盖语义安全）。
        """
        kb = load_spec_kb(
            sources=spec_sources if spec_sources is not None else SpecSources(),
            repo_root=state.get("repo_root") or None,
            diff=state["diff"],
        )
        return {"spec_kb": kb.as_state()}

    def _router(state: ReviewState) -> dict:
        """确定性路由节点（零 LLM，可调试可复现）：配置过滤 + KB 空规则（#19 接线）。

        纯规则：``experts_enabled``（= config.experts.enabled）∩ 已知专家身份，
        保序去重；KB 空（``spec_kb.documents`` 为空）再剔除 spec——Spec #12
        user story 8：没有规范文档时系统跳过规范检查而不是编造规则，「可留空」
        在图结构上落地（spec 分支此时根本不存在）。
        """
        enabled = [
            name for name in dict.fromkeys(experts_enabled) if name in EXPERT_NODES
        ]
        kb_documents = (state.get("spec_kb") or {}).get("documents") or []
        kb_empty_spec_dropped = False
        if not kb_documents and "spec" in enabled:
            enabled = [name for name in enabled if name != "spec"]
            kb_empty_spec_dropped = True
        if not enabled:
            # 显式失败而非静默放行：零分支会让图在 router 后终结，
            # 假 pass 报告比可读错误危险得多（degenerate 配置当场暴露）。
            # KB 空剔除 spec 后归零同样落此：只启用 spec 又无规范库属用法错误。
            hint = (
                "（Spec KB 为空：spec 专家不启用，无规范库不构成有效分支）"
                if kb_empty_spec_dropped
                else ""
            )
            raise ValueError(
                "experts.enabled 过滤后为空：至少启用一个已知专家"
                f"（{' / '.join(EXPERT_NODES)}）{hint}"
            )
        return {"enabled_experts": enabled}

    def _route_sends(state: ReviewState) -> list[Send]:
        """条件边：按 ``enabled_experts`` 运行时构造 Send 列表（分支数由数据决定）。

        Send arg 即该专家任务的全部输入（核心 state 的上下文切片，可与
        ReviewState 不同形）：diff + 模式 + 意图描述 + #17 组装产物——专家拿到的
        不再是裸 diff。每个 Send 独立拷贝，避免并行任务共享同一可变 dict。
        ``spec_kb_text`` 仅随 spec 专家的 Send 携带（#19：KB 是规范维度专属
        上下文，其余专家 charter 不涉规范，不注入）。
        """
        context = {
            "diff": state["diff"],
            "mode": state["mode"],
            "description": state.get("description", ""),
            "structure_map": state.get("structure_map", ""),
            "neighborhood": state.get("neighborhood", ""),
        }
        kb_text = (state.get("spec_kb") or {}).get("rendered_text", "")
        sends: list[Send] = []
        for name in state["enabled_experts"]:
            task = {**context}
            if name == "spec":
                task["spec_kb_text"] = kb_text
            sends.append(Send(EXPERT_NODES[name], task))
        return sends

    builder = StateGraph(ReviewState)
    builder.add_node("context_assembly", _assemble_context)
    builder.add_node("spec_kb", _load_spec_kb)
    builder.add_node("router", _router)
    for category, node_name in EXPERT_NODES.items():
        builder.add_node(node_name, _EXPERT_FACTORIES[category](provider))
    builder.add_node("aggregate", aggregate)
    builder.add_node("verdict", verdict_node)

    builder.add_edge(START, "context_assembly")
    builder.add_edge("context_assembly", "spec_kb")
    builder.add_edge("spec_kb", "router")
    builder.add_conditional_edges("router", _route_sends)
    # fan-in：每个专家节点完成后汇入 aggregate（并行 superstep 各写各的
    # expert_findings 增量，operator.add reducer 负责合并，aggregate 统一排序编号）
    for node_name in EXPERT_NODES.values():
        builder.add_edge(node_name, "aggregate")
    builder.add_edge("aggregate", "verdict")
    builder.add_edge("verdict", END)
    return builder.compile()
