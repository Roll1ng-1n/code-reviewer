"""Style 专家（风格维度）：吸收 nit 级问题。

「全部 nit 且 ≤3 条」双保险（Spec #18 验收标准）：

1. charter 内约束——「限定只出 nit 严重度」写进 charter 文本与严重度锚点标题
   （移植自 compare.py 的 style 臂）；
2. 代码侧保险——``experts/base.make_expert`` 解析后过滤非 nit（计数告警）+
   超上限截断（:data:`MAX_FINDINGS`）。

节点骨架见 ``experts/base.py``。
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import ModelProvider
from .base import make_expert

CATEGORY = "style"

CHARTER = "吸收 nit 级问题：命名、可维护性、文档与代码一致性、测试完备性。限定只出 nit 严重度。"

# 代码侧保险截断上限：charter 内约束之外的第二道防线
MAX_FINDINGS = 3


def make_style_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。"""
    return make_expert(
        provider,
        category=CATEGORY,
        charter=CHARTER,
        nit_only=True,
        max_findings=MAX_FINDINGS,
    )
