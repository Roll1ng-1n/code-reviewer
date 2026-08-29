"""模型抽象层：可替换 provider 缝（Spec #12：模型供应商是可替换的抽象层）。

- 密钥只从环境变量读取（user story 17）
- 专家节点只依赖 :class:`ModelProvider` 协议；测试注入脚本化假 provider 走同一缝
  （假 provider 与 CLI 缝测试套件由 实现 2/11（#14）落地）
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Protocol

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT_S = 180.0


class ModelProvider(Protocol):
    """一次 system+user 补全，返回原始文本。"""

    model_name: str

    def complete(self, *, system: str, user: str) -> str: ...


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


def make_provider(model: str | None = None) -> ModelProvider:
    """MVP 单模型决策：统一 DeepSeek（``model`` 名仅用于记录进 metadata）。"""
    return DeepSeekProvider(model or DEFAULT_MODEL)
