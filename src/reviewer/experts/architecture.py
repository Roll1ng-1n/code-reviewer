"""Architecture 专家（架构守卫维度）—— Spec #12 user story 5 的执行者。

charter 移植自对照实验原型 ``eval/golden-set/compare.py`` 的 architecture 臂；
节点骨架 / prompt 配方 / 解析容错见 ``experts/base.py``。
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import ModelProvider
from .base import make_expert

CATEGORY = "architecture"

CHARTER = (
    "审查模块边界、依赖方向、接口契约——改动是否破坏现有结构；"
    "跨实现的一致性（公共契约放宽时各实现是否跟得上）。"
)


def make_architecture_expert(provider: ModelProvider) -> Callable[[dict], dict]:
    """图节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。"""
    return make_expert(provider, category=CATEGORY, charter=CHARTER)
