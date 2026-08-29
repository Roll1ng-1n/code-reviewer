"""Spec 专家（规范一致性维度）。

本票（#18）Spec KB 恒空：charter 含「无规范库→输出空数组」指令（与
compare.py 的 spec 臂同源），router 在 enabled 列表时仍发送；KB 加载 /
「KB 空不启用」规则 / hash 由 实现 7/11（#19）接线（router 留有钩子注释）。
节点骨架见 ``experts/base.py``。
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import ModelProvider
from .base import make_expert

CATEGORY = "spec"

CHARTER = "对照成文规范逐条检查违规。本仓库无成文规范库，直接输出空数组。"


def make_spec_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。"""
    return make_expert(provider, category=CATEGORY, charter=CHARTER)
