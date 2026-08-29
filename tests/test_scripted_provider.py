"""ScriptedProvider 单元测试：三种脚本方式、调用记录、Protocol 兼容。"""

from __future__ import annotations

import pytest

from reviewer.model import ModelProvider, ScriptedProvider


def test_sequence_responses_served_in_order() -> None:
    """按调用顺序依次出队响应。"""
    provider = ScriptedProvider(["r1", "r2"])
    assert provider.complete(system="s1", user="u1") == "r1"
    assert provider.complete(system="s2", user="u2") == "r2"


def test_by_expert_routes_on_system_prompt() -> None:
    """按专家身份（system 提示词含专家 key）路由响应。"""
    provider = ScriptedProvider(by_expert={"logic": "logic-findings"})
    assert provider.complete(system="你是 logic 维度审查专家", user="u") == "logic-findings"


def test_callback_wins_over_scripts() -> None:
    """回调优先级最高，可完全自定义（含抛异常模拟模型失败）。"""
    provider = ScriptedProvider(
        ["unused"],
        callback=lambda system, user: f"cb:{system}:{user}",
    )
    assert provider.complete(system="s", user="u") == "cb:s:u"


def test_calls_record_call_context() -> None:
    """每次调用的 (system, user) 被记录，供断言调用上下文。"""
    provider = ScriptedProvider(["ok"])
    provider.complete(system="sys", user="usr")
    assert provider.calls == [("sys", "usr")]


def test_exhausted_script_fails_loudly() -> None:
    """脚本耗尽且无匹配专家身份时显式报错，不静默。"""
    provider = ScriptedProvider()
    with pytest.raises(AssertionError):
        provider.complete(system="s", user="u")


def test_satisfies_model_provider_protocol() -> None:
    """与 ModelProvider Protocol 结构兼容（runtime_checkable）。"""
    assert isinstance(ScriptedProvider(), ModelProvider)
