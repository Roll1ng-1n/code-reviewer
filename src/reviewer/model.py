"""模型抽象层：可替换 provider 缝（Spec #12：模型供应商是可替换的抽象层）。

- 密钥只从环境变量读取（user story 17）
- 专家节点只依赖 :class:`ModelProvider` 协议；:class:`ScriptedProvider` 是零网络
  脚本化假 provider（#14 落地），CLI 缝测试经 monkeypatch make_provider 注入同一缝
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from threading import Lock
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


class LlamaCppProvider:
    """进程内 GGUF 推理，只加载本地文件，不下载模型、不请求 HTTP 服务。"""

    def __init__(self, model: str, model_path: Path) -> None:
        try:
            from llama_cpp import Llama
        except (ImportError, OSError, RuntimeError) as exc:
            raise ConfigError(
                '本地推理依赖未安装或无法加载：请安装 "reviewer[local]"（或项目的 .[local]）'
            ) from exc
        self.model_name = model
        self._lock = Lock()
        try:
            self._engine = Llama(
                model_path=str(model_path), n_ctx=8192, n_gpu_layers=0, verbose=False
            )
        except Exception as exc:
            raise ConfigError(f"无法加载本地 GGUF 模型 {model_path}：{exc}") from exc

    def complete(self, *, system: str, user: str) -> str:
        # 专家图会并行调用同一 provider，llama.cpp 上下文必须串行访问。
        with self._lock:
            data = self._engine.create_chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:
        with self._lock:
            self._engine.close()


def make_provider(config: Config) -> ModelProvider:
    """按生效配置路由 DeepSeek 或进程内本地 GGUF provider。

    密钥只从 ``config.model.api_key_env`` 指定的环境变量读取（user story 17），
    不接受配置文件传入；构造期缺失 → :class:`ConfigError`（配置错误，CLI 退出码 64），
    与运行期模型调用失败（→ 70）语义区分。
    """
    if config.model.provider == "llama_cpp":
        if not config.model.path or not config.model.path.strip():
            raise ConfigError(
                "离线预检需要配置 precheck.model.path（本地 GGUF 文件）；"
                "若要使用远程 model，显式传入 --allow-network"
            )
        root = config.source.resolve().parent if config.source else Path.cwd()
        model_path = (root / config.model.path).resolve()
        if not model_path.is_file():
            raise ConfigError(f"本地模型文件不存在或不是文件：{model_path}")
        return LlamaCppProvider(config.model.name, model_path)
    if config.model.provider != "deepseek":
        raise ConfigError(
            f"model.provider 只支持 deepseek 或 llama_cpp，配置为 {config.model.provider!r}"
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
