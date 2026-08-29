"""专家共用骨架（实现 6/11 #18）：user prompt 配方、输出解析容错、图节点工厂。

四专家（architecture / logic / spec / style）只有 charter 文本与 category 常量
差异，节点函数、严重度锚点、输出契约全部共享本模块。charter 文本移植自对照
实验原型 ``eval/golden-set/compare.py`` 的 arm_panel 对应臂（勿改原型，仅移植）。

专家节点是幂等纯函数：读 state 上下文切片 → 一次模型调用 → Pydantic 校验 →
返回 ``expert_findings`` 增量（``operator.add`` reducer 汇合）。各专家只追加
自己的增量、不读不重写他人产出——并行 superstep 的更新合并顺序不保证，
覆盖语义会静默丢其他专家的写入（research/langgraph-orchestration §1.4）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..contract import Finding
from ..model import ModelProvider

# 严重度锚点（三专家共用；style 另在标题追加限定，见 build_system_prompt）
_SEVERITY_ANCHORS = """\
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃，或明确的接口契约破坏
- concern：特定条件下可能出错、边界缺失、可疑的逻辑偏差、行为与文档声明不符
- nit：风格、命名、文档、测试完备性"""

# 输出契约（移植自 compare.py CONTRACT；category 占位由本专家常量填充——
# 每个专家的 system prompt 只含自己的 category 名，模型不会替别的维度代言）
_OUTPUT_CONTRACT_TEMPLATE = (
    "## 输出契约（严格 JSON，不要输出任何其它内容；无问题则 findings 为空数组）\n"
    '{{"findings": [{{"file": "仓库相对路径（diff 头中的路径）", "line": 0, '
    '"severity": "blocker|concern|nit", "category": "{category}", '
    '"message": "一句话结论（≤120字）", '
    '"rationale": "为什么是问题，引用具体代码行为", '
    '"suggestion": "怎么改（可选）"}}]}}'
)


def build_system_prompt(category: str, charter: str, *, sev_cap: str = "") -> str:
    """组装单 charter 专家的 system prompt（语义同源 compare.py arm_panel 对应臂）。

    ``sev_cap``：附加在「严重度锚点」标题后的限定语（style 用
    「；限定只出 nit 严重度」）——charter 内约束的一部分。
    """
    return (
        f"你是资深代码审查专家，本次只负责「{category}」审查维度。\n\n"
        f"## 你的 Charter\n{charter}\n\n"
        f"## 严重度锚点{sev_cap}\n{_SEVERITY_ANCHORS}\n\n"
        "只报告有把握的问题；宁缺毋滥。"
        f'category 一律填 "{category}"。\n'
        + _OUTPUT_CONTRACT_TEMPLATE.format(category=category)
    )


def user_prompt(
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


def parse_raw_findings(raw: str) -> list[Any]:
    """解析模型原始输出为 findings 列表（容错：剥 markdown 代码围栏）。"""
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


def make_expert(
    provider: ModelProvider,
    *,
    category: str,
    charter: str,
    nit_only: bool = False,
    max_findings: int | None = None,
) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider 与 charter——测试传假 provider 即得零网络缝。

    - ``nit_only``：代码侧保险 1/2——charter 已限定只出 nit，模型越界输出
      （severity != "nit"）解析后直接丢弃并计数告警（stderr 可观测）；
    - ``max_findings``：代码侧保险 2/2——超上限截断（先过滤后截断），
      style 专家用（≤3 条双保险：charter 内约束 + 代码侧截断）。

    category 由代码钉死（``category 一律填本专家常量``）：模型输出里的
    category 字段被覆盖，不信任模型自我标注。
    """
    sev_cap = "；限定只出 nit 严重度" if nit_only else ""
    system_prompt = build_system_prompt(category, charter, sev_cap=sev_cap)

    def expert_node(state: dict) -> dict:
        raw = provider.complete(
            system=system_prompt,
            user=user_prompt(
                state["diff"],
                state.get("description", ""),
                state.get("structure_map", ""),  # #17：context-assembly 产出
                state.get("neighborhood", ""),
            ),
        )
        findings: list[dict[str, Any]] = []
        dropped_non_nit = 0
        for item in parse_raw_findings(raw):
            try:
                finding = Finding.model_validate({**item, "category": category})
            except ValidationError as exc:
                print(f"reviewer: warning: 丢弃未通过校验的 finding：{exc}", file=sys.stderr)
                continue
            if nit_only and finding.severity != "nit":
                dropped_non_nit += 1
                continue
            findings.append(finding.model_dump(exclude_none=True))
        if dropped_non_nit:
            print(
                f"reviewer: warning: {category} 专家丢弃 {dropped_non_nit} 条非 nit 发现"
                "（charter 限定只出 nit）",
                file=sys.stderr,
            )
        if max_findings is not None and len(findings) > max_findings:
            print(
                f"reviewer: warning: {category} 专家发现 {len(findings)} 条超出上限 "
                f"{max_findings} 条，截断保留前 {max_findings} 条",
                file=sys.stderr,
            )
            findings = findings[:max_findings]
        return {"expert_findings": findings}

    return expert_node
