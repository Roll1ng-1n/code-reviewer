"""单 Agent 基线专家（实现 10/11 #22）：四维 charter 融合的单次调用对照臂。

这是 Spec #12「LangGraph 工程护栏」里 ``create_agent`` 单 Agent 基线的产品化
落地。当前依赖 langgraph==1.2.6，其 ``langgraph.prebuilt`` 只暴露已弃用的
``create_react_agent``（无 ``create_agent``，后者为 2.x API）。为 MVP 简洁，
基线臂以**普通单节点实现**——语义等价于 ``create_agent`` 的单智能体：一个
节点一次模型调用，无工具循环、无多轮，输入/输出与专家团共享同一
``ReviewState`` 与 ``Finding`` schema。

与专家团（``experts/panel`` 四专家 Send fan-out）的唯一差异是**分解**：
基线把四维 charter 融合进一个 system prompt，由单一模型在一次调用里同时
覆盖 architecture / logic / spec / style，category 由模型按 finding 内容在
四类中自行判定（``parse_raw_findings`` 复用 base 的容错解析，``Finding``
Pydantic 校验把 category 限制在四类合法值内，越界丢弃并告警）。

charter 文本移植自对照实验原型 ``eval/golden-set/compare.py`` 的
``arm_baseline`` 四维融合 SYSTEM（勿改原型，仅移植）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..contract import Finding
from ..model import ModelProvider
from .base import parse_raw_findings, user_prompt

# 融合 charter 的角色标识（供 ScriptedProvider by_expert 路由匹配——与四专家
# identity 互不冲突，测试可按「融合专家」身份脚本化零网络）
FUSION_MARKER = "四维融合审查专家"

# 四维融合 charter（移植自 compare.py arm_baseline SYSTEM 的四维文案）
FUSION_CHARTER = """你是资深代码审查专家，同时负责四个审查维度：
- 架构（architecture）：审查模块边界、依赖方向、接口契约——改动是否破坏现有结构；跨实现的一致性（公共契约放宽时各实现是否跟得上）。
- 业务逻辑（logic）：对照 PR 描述的意图，审查业务逻辑的正确性与遗漏；行为与文档声明是否一致；测试是否钉住行为边界。
- 规范（spec）：对照成文规范逐条检查违规。本仓库无成文规范库，直接输出空数组。
- 风格（style）：吸收 nit 级问题：命名、可维护性、文档与代码一致性、测试完备性。限定只出 nit 严重度。"""

# 严重度锚点（与 base._SEVERITY_ANCHORS 同源；基线臂无 style「只出 nit」的
# 代码侧保险——style 只是四维之一，模型可出 blocker/concern，故不施加
# nit_only 双保险，与 compare.py arm_baseline 配方对齐）
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


def build_fusion_system_prompt() -> str:
    """组装单 Agent 基线的 system prompt（语义同源 compare.py arm_baseline SYSTEM）。"""
    return (
        f"你是{FUSION_MARKER}，资深代码审查专家，同时负责四个审查维度。\n\n"
        f"## 你的 Charter\n{FUSION_CHARTER}\n\n"
        f"## 严重度锚点\n{_SEVERITY_ANCHORS}\n\n"
        "只报告有把握的问题；宁缺毋滥。\n"
        "category 按每条 finding 的内容在 architecture/logic/spec/style 四类中"
        "自行判定。\n"
        + _OUTPUT_CONTRACT
    )


def make_baseline_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：单 Agent 融合专家（一次调用跑四维），闭包捕获 provider。

    测试传假 provider 即得零网络缝。category 由模型按内容判定（不钉死单一
    维度），``Finding`` Pydantic 校验把 category 限制在四类合法值内，越界
    丢弃并告警——与专家团节点同容错契约。
    """
    system_prompt = build_fusion_system_prompt()

    def expert_node(state: dict) -> dict:
        raw = provider.complete(
            system=system_prompt,
            user=user_prompt(
                state["diff"],
                state.get("description", ""),
                state.get("structure_map", ""),  # #17：context-assembly 产出
                state.get("neighborhood", ""),
                state.get("spec_kb_text", ""),  # 基线臂不注入 Spec KB（见 graph）
            ),
        )
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
            findings.append(finding.model_dump(exclude_none=True))
        return {"expert_findings": findings}

    return expert_node
