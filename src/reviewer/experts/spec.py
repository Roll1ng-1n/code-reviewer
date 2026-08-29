"""Spec 专家（规范一致性维度，#19 接线 Spec KB）。

KB 非空时由 router 启用（KB 空 → spec 分支在图结构上不存在——「可留空」
落图，Spec #12 user story 8：没有规范文档时跳过规范检查而不是编造规则）；
KB 全文经 Send 的 ``spec_kb_text`` 注入 user prompt 的「Spec KB」块，charter
指令按「文档名 § 节名」引用章节依据（ADR-0001 文档式存储的引用格式）。
节点骨架见 ``experts/base.py``。
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import ModelProvider
from .base import make_expert

CATEGORY = "spec"

CHARTER = (
    "对照规范知识库（Spec KB）逐条检查违规。规范文档全文在用户消息的「Spec KB」块："
    "每个文档以「### 文档：<文档名>」标界，章节为文档内「## <节名>」标题"
    "（降级注入时章节直接标为「<文档名> § <节名>」）。发现违规时，rationale 必须"
    "引用所违反的具体章节，格式为「文档名 § 节名」。"
)


def make_spec_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。"""
    return make_expert(provider, category=CATEGORY, charter=CHARTER)
