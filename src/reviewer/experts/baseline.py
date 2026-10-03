"""一次融合审查：与 Expert Panel 共享 charter、启用维度、上下文和 style 护栏。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from langchain.agents import create_agent

from ..contract import Finding
from ..model import ModelProvider, ProviderChatModel
from .base import limit_findings, parse_raw_findings, user_prompt
from . import architecture, logic, spec, style

# 融合 charter 的角色标识（供 ScriptedProvider by_expert 路由匹配——与四专家
# identity 互不冲突，测试可按「融合专家」身份脚本化零网络）
FUSION_MARKER = "四维融合审查专家"

# 四维融合 charter（移植自 compare.py arm_baseline SYSTEM 的四维文案）
_CHARTERS = {
    "architecture": ("架构", architecture.CHARTER),
    "logic": ("业务逻辑", logic.CHARTER),
    "spec": ("规范", spec.CHARTER),
    "style": ("风格", style.CHARTER + f"最多报告 {style.MAX_FINDINGS} 条。"),
}

# 严重度锚点；style 维度另施加与 panel 相同的代码护栏。
_SEVERITY_ANCHORS = """\
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃，或明确的接口契约破坏——不修不能合并
- concern：特定条件下可能出错、边界 / 健壮性缺失、可疑的逻辑偏差、行为与文档声明不符——需作者回应
- nit：风格、命名、文档、测试完备性——不阻塞"""

# 输出契约（category 四选一，与 compare.py CONTRACT 对齐）
_OUTPUT_CONTRACT = (
    "## 输出契约（严格 JSON，不要输出任何其它内容；无问题则 findings 为空数组）\n"
    '{"findings": [{"file": "仓库相对路径（diff 头中的路径）", "line": 0, '
    '"severity": "blocker|concern|nit", '
    '"category": "architecture|logic|spec|style", '
    '"message": "一句话结论（≤120字）", '
    '"rationale": "为什么是问题，引用具体代码行为", '
    '"suggestion": "怎么改（可选）"}}]'
)


def build_fusion_system_prompt(enabled: list[str] | None = None) -> str:
    """组装单 Agent 基线的 system prompt（语义同源 compare.py arm_baseline SYSTEM）。"""
    enabled = list(_CHARTERS) if enabled is None else enabled
    charter = "\n".join(f"- {_CHARTERS[name][0]}（{name}）：{_CHARTERS[name][1]}" for name in enabled)
    if "spec" not in enabled:
        charter += "\n规范（spec）未启用；不作规范判断，不编造规范。"
    return (
        f"你是{FUSION_MARKER}，负责以下已启用审查维度。\n\n"
        f"## 你的 Charter\n{charter}\n\n"
        f"## 严重度锚点\n{_SEVERITY_ANCHORS}\n\n"
        "只报告有把握的问题；宁缺毋滥。\n"
        f"category 仅可取已启用维度：{' / '.join(enabled)}。\n"
        + _OUTPUT_CONTRACT
    )


def make_baseline_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：单 Agent 融合专家（一次调用跑四维），闭包捕获 provider。

    测试传假 provider 即得零网络缝。category 由模型按内容判定（不钉死单一
    维度），``Finding`` Pydantic 校验把 category 限制在四类合法值内，越界
    丢弃并告警——与专家团节点同容错契约。
    """
    def expert_node(state: dict) -> dict:
        enabled = state.get("enabled_experts", [])
        agent = create_agent(ProviderChatModel(provider), tools=[],
                             system_prompt=build_fusion_system_prompt(enabled))
        result = agent.invoke({"messages": [{"role": "user", "content": user_prompt(
                state["diff"],
                state.get("description", ""),
                state.get("structure_map", ""),  # #17：context-assembly 产出
                state.get("neighborhood", ""),
                state.get("spec_kb_text", ""),
            )}]})
        raw = result["messages"][-1].content
        if not isinstance(raw, str):
            raise ValueError("Baseline model returned non-text content")
        findings: list[dict[str, Any]] = []
        for item in parse_raw_findings(raw):
            try:
                finding = Finding.model_validate(item)
            except ValidationError as exc:
                print(
                    f"reviewer: warning: 丢弃未通过校验的 finding：{exc}",
                    file=sys.stderr,
                )
                continue
            if finding.category not in enabled:
                print(f"reviewer: warning: 丢弃未启用维度 {finding.category} 的 finding", file=sys.stderr)
                continue
            findings.append(finding.model_dump(exclude_none=True))
        styles = limit_findings([f for f in findings if f["category"] == "style"],
                                category="style", nit_only=True, max_findings=style.MAX_FINDINGS)
        findings = [f for f in findings if f["category"] != "style"] + styles
        return {"expert_findings": findings}

    return expert_node
