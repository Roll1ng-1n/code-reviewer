"""配置分层：.reviewer.yaml 的发现、解析与内置缺省（Spec #12 / 实现 3/11 #15）。

四层优先级：CLI 参数 > 子命令默认 > 配置文件 > 内置缺省。本模块只负责后两层：
把 cwd 下的 ``.reviewer.yaml`` 解析成 :class:`Config`（文件不存在 = 全内置缺省）；
mode 与子命令默认的合并由 CLI 层完成（cli._resolve_mode）。

- 配置发现只看 cwd，不看 pyproject.toml 等 Python 工程标记（非 Python 仓库同样可用）；
- 密钥永不进配置文件：只从 ``model.api_key_env`` 指定的环境变量读取（user story 17）；
- ``base`` / ``spec_kb.paths`` / ``experts.enabled`` 本票只解析与暴露，
  消费方分别是 #16（merge-base）、#19（Spec KB 来源层）与 #18（router 过滤）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE_NAME = ".reviewer.yaml"

# mode 的合法取值（与 CLI --mode 的 choices 一致）
MODES = ("mentor", "gatekeeper")


@dataclass
class SpecKbConfig:
    """Spec KB 配置节：规范文档目录（相对 cwd），可留空。"""

    paths: list[str] = field(default_factory=lambda: ["specs/"])


@dataclass
class ModelConfig:
    """模型配置节：provider/name 可配；密钥只走 api_key_env 指定的环境变量。"""

    provider: str = "deepseek"
    name: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"


@dataclass
class ExpertsConfig:
    """专家配置节：启用的专家身份（与 contract.Category 同名）。"""

    enabled: list[str] = field(
        default_factory=lambda: ["architecture", "logic", "spec", "style"]
    )


@dataclass
class Config:
    """一份生效配置（「配置文件 + 内置缺省」两层合并的结果）。

    ``mode`` 为 None 表示配置层不表态，交由 CLI 层的子命令默认决定。
    """

    mode: str | None = None
    base: str = "main"
    spec_kb: SpecKbConfig = field(default_factory=SpecKbConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    experts: ExpertsConfig = field(default_factory=ExpertsConfig)
    source: Path | None = None  # 配置文件路径；未发现文件时 None


class ConfigError(Exception):
    """配置非法（YAML 语法错/字段类型错）或密钥缺失；CLI 层映射为退出码 64。"""


def load_config(cwd: Path | None = None) -> Config:
    """从 ``cwd``（缺省为进程 cwd）发现 ``.reviewer.yaml`` 并解析为 :class:`Config`。

    文件不存在（或为空文件）→ 全内置缺省；文件非法（YAML 语法错/字段类型错）→
    :class:`ConfigError`，错误信息指向文件与字段。部分字段缺省时按节合并内置缺省。
    """
    root = Path.cwd() if cwd is None else cwd
    path = root / CONFIG_FILE_NAME
    if not path.is_file():
        return Config()
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 {path} 不是合法 YAML：{exc}") from exc
    if doc is None:  # 空文件 = 全缺省
        return Config(source=path)
    if not isinstance(doc, dict):
        raise ConfigError(
            f"配置文件 {path} 顶层必须是键值映射，实际是 {_type_name(doc)}"
        )
    config = Config(source=path)
    config.mode = _parse_mode(doc.get("mode"), path)
    if "base" in doc:
        config.base = _parse_str(doc["base"], "base", path)
    config.spec_kb = _parse_spec_kb(doc.get("spec_kb"), path)
    config.model = _parse_model(doc.get("model"), path)
    config.experts = _parse_experts(doc.get("experts"), path)
    return config


def _type_name(value: Any) -> str:
    return type(value).__name__


def _parse_str(value: Any, label: str, path: Path) -> str:
    if not isinstance(value, str):
        raise ConfigError(
            f"配置文件 {path} 字段 {label} 类型错误：应为字符串，实际是 {_type_name(value)}"
        )
    return value


def _parse_str_list(value: Any, label: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(
            f"配置文件 {path} 字段 {label} 类型错误：应为字符串列表，"
            f"实际是 {_type_name(value)}"
        )
    return list(value)


def _parse_mode(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in MODES:
        expected = " 或 ".join(MODES)
        raise ConfigError(
            f"配置文件 {path} 字段 mode 类型错误：应为 {expected}"
            f"（或不写 = 交由子命令默认），实际是 {value!r}"
        )
    return value


def _parse_section(value: Any, label: str, path: Path) -> dict[str, Any]:
    """配置节必须是映射；未写该节 → 空映射（节内字段全部吃内置缺省）。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"配置文件 {path} 字段 {label} 类型错误：应为键值映射，"
            f"实际是 {_type_name(value)}"
        )
    return value


def _parse_spec_kb(value: Any, path: Path) -> SpecKbConfig:
    section = _parse_section(value, "spec_kb", path)
    defaults = SpecKbConfig()
    if "paths" in section:
        defaults.paths = _parse_str_list(section["paths"], "spec_kb.paths", path)
    return defaults


def _parse_model(value: Any, path: Path) -> ModelConfig:
    section = _parse_section(value, "model", path)
    defaults = ModelConfig()
    if "provider" in section:
        defaults.provider = _parse_str(section["provider"], "model.provider", path)
    if "name" in section:
        defaults.name = _parse_str(section["name"], "model.name", path)
    if "api_key_env" in section:
        defaults.api_key_env = _parse_str(
            section["api_key_env"], "model.api_key_env", path
        )
    return defaults


def _parse_experts(value: Any, path: Path) -> ExpertsConfig:
    section = _parse_section(value, "experts", path)
    defaults = ExpertsConfig()
    if "enabled" in section:
        defaults.enabled = _parse_str_list(section["enabled"], "experts.enabled", path)
    return defaults
