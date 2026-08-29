"""Logic 专家（核心专家）。

charter（Spec #12）：对照意图描述的正确性与遗漏、行为与文档一致性、测试钉边界。
曳光弹版：固定启用、一次 LLM 调用（经模型抽象层）、Pydantic 校验后入 state。

专家节点设计为幂等纯函数（findings 全量重算，无副作用）——interrupt 恢复时
节点从头重跑的官方语义下这是必要属性（research/langgraph-orchestration §4.3）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..contract import Finding
from ..model import ModelProvider

CATEGORY = "logic"

_SYSTEM_PROMPT = """你是资深代码审查专家，负责「业务逻辑一致性」（logic）审查维度。

## 职责（charter）
- 对照意图描述，检查改动的正确性与遗漏
- 改动行为与文档/注释声明的一致性
- 测试是否钉住行为边界

只报告本维度发现；架构、规范、风格由其他专家负责，不要越界。

## 严重度锚点
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃——不修不能合并
- concern：特定条件下可能出错、边界/健壮性缺失、可疑的业务逻辑偏差——需作者回应
- nit：风格、命名、可维护性——不阻塞

## 输出契约（严格 JSON，不输出任何其它内容；无问题则 findings 为空数组）
{"findings": [{"file": "仓库相对路径", "line": 42, "severity": "blocker|concern|nit", "category": "logic", "message": "一句话结论（≤120字）", "rationale": "为什么是问题：引用具体代码行为或文档声明", "suggestion": "怎么改（可选）"}]}
"""


def _user_prompt(
    diff: str, description: str, structure_map: str = "", neighborhood: str = ""
) -> str:
    """配方形态 user prompt（#17，语义同源 prototype/risk-p1/run.py 的 build_prompts）：

    结构地图块 + diff 块 + 描述块 + 邻域块；无对应上下文时该块整体省略，
    不留空标题（严重度锚点与输出契约在 system prompt，恒在）。
    """
    parts: list[str] = []
    if structure_map.strip():
        parts.append(f"## 仓库结构地图\n{structure_map.strip()}")
    parts.append(f"## 变更 diff（unified，行号为新侧）\n```diff\n{diff}\n```")
    if description.strip():
        parts.append(f"## 意图描述\n{description.strip()}")
    if neighborhood.strip():
        parts.append(f"## import 邻域（被改文件的直接依赖）\n{neighborhood.strip()}")
    parts.append("请审查这个改动。")
    return "\n\n".join(parts)


def _parse_raw_findings(raw: str) -> list[Any]:
    text = raw.strip()
    if text.startswith("```"):  # 容错：剥 markdown 代码围栏
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else text.strip("`")
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -3]
    payload = json.loads(text.strip())
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise ValueError("模型输出缺少 findings 数组")
    return payload["findings"]


def make_logic_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。"""

    def expert_logic(state: dict) -> dict:
        raw = provider.complete(
            system=_SYSTEM_PROMPT,
            user=_user_prompt(
                state["diff"],
                state.get("description", ""),
                state.get("structure_map", ""),  # #17：context-assembly 产出
                state.get("neighborhood", ""),
            ),
        )
        validated: list[dict[str, Any]] = []
        for item in _parse_raw_findings(raw):
            try:
                # category 由 charter 钉死：本专家只产出 logic 维度发现
                finding = Finding.model_validate({**item, "category": CATEGORY})
            except ValidationError as exc:
                print(f"reviewer: warning: 丢弃未通过校验的 finding：{exc}", file=sys.stderr)
                continue
            validated.append(finding.model_dump(exclude_none=True))
        return {"expert_findings": validated}

    return expert_logic
