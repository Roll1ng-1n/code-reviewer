"""Logic 专家（核心专家）：业务逻辑一致性维度。

charter 移植自对照实验原型 ``eval/golden-set/compare.py`` 的 logic 臂
（原曳光弹版文案同源）；节点骨架 / prompt 配方 / 解析容错见 ``experts/base.py``。

专家节点设计为幂等纯函数（findings 增量返回，无副作用）——interrupt 恢复时
节点从头重跑的官方语义下这是必要属性（research/langgraph-orchestration §4.3）。
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import ModelProvider
from .base import make_expert

CATEGORY = "logic"

CHARTER = (
    "对照 PR 描述的意图，审查业务逻辑的正确性与遗漏；"
    "行为与文档声明是否一致；测试是否钉住行为边界。"
)


def make_logic_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。"""
    return make_expert(provider, category=CATEGORY, charter=CHARTER)
