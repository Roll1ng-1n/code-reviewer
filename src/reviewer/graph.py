"""评审图 —— Spec #12 拓扑（实现 6/11 #18 专家团 Send fan-out + 实现 7/11 #19
Spec KB + 实现 10/11 #22 单 Agent 基线臂）::

    START → context_assembly（#17：结构地图 + import 邻域，纯本地零模型）
    → spec_kb（#19：三层来源加载，KB 空 = 合法态）→ router（纯规则，零 LLM）
    —[Send]→ 专家团四专家并行（arm=panel）或 单一融合专家（arm=baseline，#22）
    → aggregate → verdict → END

- router：``config.experts.enabled`` 过滤出 ``enabled_experts``（确定性、可复现），
  分支数运行时决定——条件边据此返回 Send 列表；KB 空规则（#19 接线）：
  ``spec_kb.documents`` 为空 → 从 enabled 中剔除 spec，「可留空」在图结构上落地。
- spec-kb 节点：``load_spec_kb``（src/reviewer/spec_kb.py）三层来源合并 +
  sha256 去重 + >500 行降级，产物整体写入 ``spec_kb``（单写者，覆盖语义安全）。
- 专家团（arm=panel）：单 charter 单 category（charter 移植自
  eval/golden-set/compare.py 的 arm_panel 对应臂），各自返回增量
  ``expert_findings``，经 ``operator.add`` reducer 汇合——并行输出零静默丢失。
- 单 Agent 基线（arm=baseline，#22）：单一融合专家一次调用跑四维
  （charter 移植自 compare.py 的 arm_baseline SYSTEM），category 由模型按
  finding 内容在四类中自行判定；同样写 ``expert_findings`` 汇入 aggregate。
- 两臂共享 context-assembly、spec-kb、aggregate（含复核过滤）、verdict、render——
  唯一差异是分解（专家团 = 四专家并行；基线 = 单一融合专家一次调用）。

#23 ``--interactive`` interrupt()（实验性，默认关闭）：interactive=True 时在
aggregate 之后插入 ``confirm`` 节点——逐条 finding 经 ``interrupt()`` 请求人工
确认，CLI 侧 ``Command(resume=...)`` 携决策恢复（幂等透传，见 ``_confirm``）。
"""

from __future__ import annotations

from collections.abc import Sequence

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .aggregate import make_aggregator
from .context import assemble_context
from .experts.architecture import make_architecture_expert
from .experts.baseline import make_baseline_expert
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

# 单 Agent 基线节点名（#22）
BASELINE_NODE = "expert_baseline"


def _assemble_context(state: ReviewState) -> dict:
    """context-assembly 节点（#17）：diff 读入之后、专家之前的上下文组装。

    纯本地计算（os.walk + stdlib ast，零模型调用）：结构地图 / import 邻域 /
    预算统计写入共享 state（用途见 state.py docstring）。repo_root 缺失
    （纯回放无快照）时两块上下文为空、计数 0，不致命。
    """
    return assemble_context(
        diff=state["diff"],
        repo_root=state.get("repo_root") or None,
        description=state.get("description", ""),
    )


def _confirm(state: ReviewState) -> dict:
    """confirm 节点（#23 --interactive 实验）：逐条 finding 请求人工确认。

    **幂等设计（关键，调研 #2 §4.3）**：``interrupt()`` 恢复时会从头重跑被中断
    节点——若节点在 interrupt 前有任何副作用（如已写过 state、发过网络请求），
    重跑会重复执行。本节点把「是否已确认」标记放在 ``state["confirmed"]``，
    首次进入时该标记为 False → 走 interrupt 挂起等人工决策；恢复时 state 已带
    ``confirmed=True``（由本次 interrupt 恢复后返回的更新写入）→ 直接透传已保存
    的 ``kept``，不再二次 interrupt。节点自身在 interrupt 之前**零副作用**
    （不写 state、不调模型），因此重跑无重复 finding、无重复调用。

    交互契约：批量一次 ``interrupt({"findings": [...]})`` 返回全部 findings（已
    带 F001.. 序号，见 aggregate 收尾编号），CLI 侧渲染确认列表、读用户决策
    （``a`` 全通过 / ``d`` 全丢弃 / ``1,3`` 保留选中 / ``q`` 中止），再以
    ``Command(resume=保留列表)`` 恢复——resume 值成为 ``interrupt()`` 返回值，
    即「保留的 findings 列表」。本节点把该列表写入 ``kept``（并置 ``confirmed``
    为 True）供 verdict 消费。
    """
    if state.get("confirmed"):
        # 恢复重跑：已确认 → 直接透传上次决策结果，绝不二次 interrupt（幂等）
        return {"kept": state.get("kept", [])}
    findings = state.get("findings", [])
    kept = interrupt({"findings": findings})  # 挂起等人工；resume 值 = 保留列表
    return {"confirmed": True, "kept": kept}


def _findings_for_verdict(state: ReviewState) -> dict:
    """verdict 前分流节点：interactive 模式用 confirm 的 ``kept``，批处理用 ``findings``。

    纯本地、零副作用；仅 interactive 图路径挂载（见 build_review_graph），
    避免给默认批处理路径引入任何额外节点。
    """
    return {"findings": state.get("kept", state.get("findings", []))}


def build_review_graph(
    provider: ModelProvider,
    *,
    experts_enabled: Sequence[str],
    spec_sources: SpecSources | None = None,
    arm: str = "panel",
    checkpointer=None,
    interactive: bool = False,
):
    """编译评审图。provider 经闭包注入——测试传假 provider 即得零网络缝。

    ``experts_enabled``：router 的纯规则输入（CLI 传 ``config.experts.enabled``），
    决定 fan-out 的候选集合；实际分支数由 router 运行时过滤结果决定。
    ``spec_sources``：#19 三层来源的 CLI/config 侧打包（None = 无来源 → KB 恒空）。
    ``arm``（#22）：``"panel"`` = 专家团四专家并行（默认）；``"baseline"`` =
    单 Agent 融合专家一次调用。两臂共享 context-assembly / spec-kb / aggregate /
    verdict / render，唯一差异是分解。
    ``checkpointer``（#23）：透传 ``.compile(checkpointer=...)``——仅 --interactive
    开启时 CLI 才构造 SqliteSaver 并传入，默认批处理路径保持 None（零持久化开销）。
    ``interactive``（#23，实验性）：True 时在 aggregate 之后、verdict 之前插入
    ``confirm`` 节点——对每条 finding 用 ``interrupt()`` 请求人工确认（批量一次
    interrupt 返回全部 findings + 序号），CLI 侧渲染确认列表读用户决策后
    ``Command(resume=...)`` 恢复，confirm 节点按决策过滤 findings 再进 verdict。
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

        两臂使用相同启停和 KB 空过滤规则。
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
        return {"enabled_experts": enabled, "arm": arm}

    def _route_sends(state: ReviewState) -> list[Send]:
        """条件边：按 ``arm`` 运行时构造 Send 列表（分支数由数据/臂决定）。

        - ``arm == "baseline"``：单个 Send → ``expert_baseline``（融合专家），
          task 与专家团同形，并携带实际启用维度及 spec 维度的 Spec KB。
        - ``arm == "panel"``：按 ``enabled_experts`` fan-out 四专家并行，
          ``spec_kb_text`` 仅随 spec 专家 Send 携带（#19）。
        """
        context = {
            "diff": state["diff"],
            "mode": state["mode"],
            "description": state.get("description", ""),
            "structure_map": state.get("structure_map", ""),
            "neighborhood": state.get("neighborhood", ""),
        }
        kb_text = (state.get("spec_kb") or {}).get("rendered_text", "")
        if state.get("arm") == "baseline":
            return [Send(BASELINE_NODE, {**context,
                "enabled_experts": state["enabled_experts"],
                "spec_kb_text": kb_text if "spec" in state["enabled_experts"] else "",
            })]
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
    builder.add_node(BASELINE_NODE, make_baseline_expert(provider))
    builder.add_node("aggregate", make_aggregator(provider))
    builder.add_node("verdict", verdict_node)

    builder.add_edge(START, "context_assembly")
    builder.add_edge("context_assembly", "spec_kb")
    builder.add_edge("spec_kb", "router")
    builder.add_conditional_edges("router", _route_sends)
    # fan-in：每个专家节点（含 baseline 单节点）完成后汇入 aggregate（并行
    # superstep 各写各的 expert_findings 增量，operator.add reducer 负责合并，
    # aggregate 统一排序编号）
    for node_name in EXPERT_NODES.values():
        builder.add_edge(node_name, "aggregate")
    builder.add_edge(BASELINE_NODE, "aggregate")
    if interactive:
        # #23：aggregate → confirm（逐条人工确认）→ verdict。confirm 节点用
        # interrupt() 挂起，恢复后按决策过滤 findings 再进 verdict。
        # 批处理路径（interactive=False）不挂 confirm，保持 aggregate→verdict
        # 直连零额外节点——默认路径严格不受影响。
        builder.add_node("confirm", _confirm)
        builder.add_node("confirm_filter", _findings_for_verdict)
        builder.add_edge("aggregate", "confirm")
        builder.add_edge("confirm", "confirm_filter")
        builder.add_edge("confirm_filter", "verdict")
    else:
        builder.add_edge("aggregate", "verdict")
    builder.add_edge("verdict", END)
    return builder.compile(checkpointer=checkpointer)
