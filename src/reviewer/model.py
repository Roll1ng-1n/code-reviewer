"""模型抽象层：可替换 provider 缝（Spec #12：模型供应商是可替换的抽象层）。

- 密钥只从环境变量读取（user story 17）
- 专家节点只依赖 :class:`ModelProvider` 协议；:class:`ScriptedProvider` 是零网络
  脚本化假 provider（#14 落地），CLI 缝测试经 monkeypatch make_provider 注入同一缝
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from .config import Config, ConfigError

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT_S = 180.0


@runtime_checkable
class ModelProvider(Protocol):
    """一次 system+user 补全，返回原始文本。"""

    model_name: str

    def complete(self, *, system: str, user: str) -> str: ...


class ProviderChatModel(BaseChatModel):
    """Adapt the same ModelProvider to create_agent without a second API client."""

    _provider: ModelProvider = PrivateAttr()

    def __init__(self, provider: ModelProvider):
        super().__init__()
        self._provider = provider

    @property
    def _llm_type(self) -> str:
        return "reviewer-model-provider"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self._provider.model_name}

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        if any(not isinstance(message, (SystemMessage, HumanMessage))
               or not isinstance(message.content, str) for message in messages):
            raise ValueError("Baseline accepts text system/user messages only")
        system = "\n\n".join(message.content for message in messages if isinstance(message, SystemMessage))
        user = "\n\n".join(message.content for message in messages if isinstance(message, HumanMessage))
        raw = self._provider.complete(system=system, user=user)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=raw))])


class DeepSeekProvider:
    """MVP 单模型（deepseek-chat），OpenAI 兼容 chat/completions，仅标准库。"""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY 环境变量（密钥只从环境变量读取，不写入配置文件）"
            )
        self.model_name = model
        self.api_key = key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def complete(self, *, system: str, user: str) -> str:
        payload = json.dumps(
            {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


def make_provider(config: Config) -> ModelProvider:
    """按配置路由 provider（#15）：MVP 仅支持 deepseek。

    密钥只从 ``config.model.api_key_env`` 指定的环境变量读取（user story 17），
    不接受配置文件传入；构造期缺失 → :class:`ConfigError`（配置错误，CLI 退出码 64），
    与运行期模型调用失败（→ 70）语义区分。
    """
    if config.model.provider != "deepseek":
        raise ConfigError(
            f"model.provider 暂只支持 \"deepseek\"（MVP 单模型），配置为 {config.model.provider!r}"
        )
    key = os.environ.get(config.model.api_key_env)
    if not key:
        raise ConfigError(
            f"环境变量 {config.model.api_key_env} 未设置：密钥只从环境变量读取，"
            "不写入配置文件（如需换环境变量名，改配置文件 model.api_key_env）"
        )
    return DeepSeekProvider(config.model.name, api_key=key)


class ScriptedProvider:
    """脚本化假 provider：按调用序列/专家身份/回调返回预定响应，全程零网络。

    三种脚本方式按优先级生效：

    1. ``callback``：可编程回调 ``(system, user) -> str``，完全自定义行为
       （含抛异常模拟模型调用失败）；
    2. ``by_expert``：专家身份 → 响应文本。key 需出现在该专家的 system
       提示词中（如 ``"logic"``），即「按调用上下文路由响应」；
    3. ``responses``：按调用顺序依次出队的响应队列。

    每次调用的 ``(system, user)`` 记录在 ``calls``，供测试断言调用上下文。
    实现放在产品模块而非 tests/：它同时是 #10 基线臂与未来集成测试的公共设施。
    """

    def __init__(
        self,
        responses: Sequence[str] = (),
        *,
        by_expert: dict[str, str] | None = None,
        callback: Callable[[str, str], str] | None = None,
        model_name: str = "scripted-fake",
    ) -> None:
        self.model_name = model_name
        self._responses = list(responses)
        self._by_expert = dict(by_expert) if by_expert else {}
        self._callback = callback
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self._callback is not None:
            return self._callback(system, user)
        for expert, response in self._by_expert.items():
            if expert in system:
                return response
        if self._responses:
            return self._responses.pop(0)
        raise AssertionError(
            "ScriptedProvider：脚本耗尽且无匹配专家身份——请补足脚本"
        )
