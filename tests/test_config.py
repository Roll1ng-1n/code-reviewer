"""#15 配置分层：.reviewer.yaml 发现/解析、四层优先级、密钥只走环境变量。

四层优先级：CLI 参数 > 子命令默认 > 配置文件 > 内置缺省。
load_config 层验证「配置文件 > 内置缺省」（按节合并）；CLI 缝层验证 mode
解析链两个关键用例（子命令默认压过 config.mode、--mode 压过一切）、
配置错误 → 64、构造期缺 key → 64（与运行期模型失败 → 70 语义区分）。
"""

from __future__ import annotations

import json
from pathlib import Path

from reviewer.config import (
    CONFIG_FILE_NAME,
    Config,
    ConfigError,
    ModelConfig,
    load_config,
)
from reviewer.model import DeepSeekProvider, ScriptedProvider, make_provider

# 全量合法配置样例（覆盖全部配置节）
FULL_CONFIG = """\
mode: mentor
base: develop
spec_kb:
  paths: ["docs/specs", "team-rules"]
model:
  provider: deepseek
  name: deepseek-reasoner
  api_key_env: MY_REVIEWER_KEY
experts:
  enabled: [architecture, logic]
"""


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / CONFIG_FILE_NAME
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadConfigDefaults:
    """「内置缺省」层：无文件/空文件/部分配置的合并行为。"""

    def test_missing_file_returns_builtin_defaults(self, tmp_path: Path) -> None:
        """cwd 无 .reviewer.yaml → 全内置缺省。"""
        config = load_config(tmp_path)
        assert config.mode is None  # 交由子命令默认
        assert config.base == "main"
        assert config.spec_kb.paths == ["specs/"]
        assert config.model.provider == "deepseek"
        assert config.model.name == "deepseek-chat"
        assert config.model.api_key_env == "DEEPSEEK_API_KEY"
        assert config.experts.enabled == ["architecture", "logic", "spec", "style"]
        assert config.source is None

    def test_empty_file_means_defaults(self, tmp_path: Path) -> None:
        """空文件等价于无文件（source 记录文件路径）。"""
        _write_config(tmp_path, "")
        config = load_config(tmp_path)
        assert config.base == "main"
        assert config.model.api_key_env == "DEEPSEEK_API_KEY"
        assert config.source == tmp_path / CONFIG_FILE_NAME

    def test_full_config_parsed(self, tmp_path: Path) -> None:
        """全部配置节按规格解析。"""
        _write_config(tmp_path, FULL_CONFIG)
        config = load_config(tmp_path)
        assert config.mode == "mentor"
        assert config.base == "develop"
        assert config.spec_kb.paths == ["docs/specs", "team-rules"]
        assert config.model.provider == "deepseek"
        assert config.model.name == "deepseek-reasoner"
        assert config.model.api_key_env == "MY_REVIEWER_KEY"
        assert config.experts.enabled == ["architecture", "logic"]
        assert config.source == tmp_path / CONFIG_FILE_NAME

    def test_partial_config_keeps_defaults_for_unspecified(self, tmp_path: Path) -> None:
        """只写部分字段：节内未写字段与其余节全部吃内置缺省（按节合并）。"""
        _write_config(tmp_path, "model:\n  name: other-chat\n")
        config = load_config(tmp_path)
        assert config.model.name == "other-chat"
        assert config.model.provider == "deepseek"
        assert config.model.api_key_env == "DEEPSEEK_API_KEY"
        assert config.spec_kb.paths == ["specs/"]
        assert config.base == "main"
        assert config.mode is None


class TestLoadConfigErrors:
    """非法配置 → ConfigError，错误信息指向文件与字段。"""

    def test_yaml_syntax_error_points_at_file(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "model: [unclosed\n  bad")
        try:
            load_config(tmp_path)
        except ConfigError as exc:
            assert CONFIG_FILE_NAME in str(exc)

    def test_top_level_not_mapping(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "- just\n- a\n- list\n")
        try:
            load_config(tmp_path)
        except ConfigError as exc:
            assert "顶层" in str(exc)

    def test_section_not_mapping(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "model: deepseek\n")
        try:
            load_config(tmp_path)
        except ConfigError as exc:
            assert "model" in str(exc)

    def test_nested_list_field_type_error(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "spec_kb:\n  paths: specs\n")
        try:
            load_config(tmp_path)
        except ConfigError as exc:
            assert "spec_kb.paths" in str(exc)

    def test_scalar_field_type_error(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "base: 42\n")
        try:
            load_config(tmp_path)
        except ConfigError as exc:
            assert "base" in str(exc)

    def test_mode_bad_value(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "mode: bogus\n")
        try:
            load_config(tmp_path)
        except ConfigError as exc:
            assert "mode" in str(exc)


class TestDiscovery:
    """配置发现只看 cwd，不看 Python 工程标记（非 Python 仓库可用）。"""

    def test_discovery_depends_only_on_cwd_not_pyproject(self, tmp_path: Path) -> None:
        """有 pyproject.toml 无 .reviewer.yaml → 全缺省；有 yaml 即生效。"""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'x'\n", encoding="utf-8"
        )
        assert load_config(tmp_path).base == "main"
        _write_config(tmp_path, "base: trunk\n")
        assert load_config(tmp_path).base == "trunk"

    def test_cwd_default_is_process_cwd(self, monkeypatch, tmp_path: Path) -> None:
        """cwd 缺省取进程 cwd（CLI 启动即发现仓库根配置）。"""
        _write_config(tmp_path, "base: trunk\n")
        monkeypatch.chdir(tmp_path)
        assert load_config().base == "trunk"


class TestMakeProviderRouting:
    """make_provider(config)：provider/name/api_key_env 路由，密钥只走环境变量。"""

    def test_deepseek_routed_with_config_name_and_key(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k-unit")
        provider = make_provider(Config())  # 全缺省配置
        assert isinstance(provider, DeepSeekProvider)
        assert provider.model_name == "deepseek-chat"
        assert provider.api_key == "k-unit"

    def test_custom_api_key_env_name(self, monkeypatch) -> None:
        """api_key_env 自定义环境变量名生效。"""
        monkeypatch.setenv("ALT_KEY", "alt-secret")
        config = Config(model=ModelConfig(api_key_env="ALT_KEY"))
        provider = make_provider(config)
        assert isinstance(provider, DeepSeekProvider)
        assert provider.api_key == "alt-secret"

    def test_missing_key_names_env_var(self, monkeypatch) -> None:
        """缺 key → ConfigError，错误指名哪个环境变量未设。"""
        monkeypatch.delenv("MISSING_KEY", raising=False)
        config = Config(model=ModelConfig(api_key_env="MISSING_KEY"))
        try:
            make_provider(config)
        except ConfigError as exc:
            assert "MISSING_KEY" in str(exc)

    def test_unknown_provider_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        config = Config(model=ModelConfig(provider="openai"))
        try:
            make_provider(config)
        except ConfigError as exc:
            assert "openai" in str(exc)


class TestCliConfigWiring:
    """CLI 缝层：配置文件层压过内置缺省、错误路径退出码。"""

    def test_config_file_reaches_provider_seam(
        self, run_cli, monkeypatch, tmp_path, diff_file, findings_json
    ) -> None:
        """yaml 的 model.name / base 送达 make_provider（配置文件层 > 内置缺省层）。"""
        _write_config(tmp_path, "base: trunk\nmodel:\n  name: cfg-chat\n")
        monkeypatch.chdir(tmp_path)
        provider = ScriptedProvider([findings_json([])])
        code, _, _, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
        assert code == 0
        config = run_cli.configs[-1]
        assert config.model.name == "cfg-chat"
        assert config.base == "trunk"
        assert config.source == tmp_path / CONFIG_FILE_NAME

    def test_builtin_defaults_without_config_file(
        self, run_cli, monkeypatch, tmp_path, diff_file, findings_json
    ) -> None:
        """无配置文件 → make_provider 收到全内置缺省 Config。"""
        monkeypatch.chdir(tmp_path)
        provider = ScriptedProvider([findings_json([])])
        code, _, _, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
        assert code == 0
        config = run_cli.configs[-1]
        assert config.model.name == "deepseek-chat"
        assert config.base == "main"
        assert config.experts.enabled == ["architecture", "logic", "spec", "style"]

    def test_invalid_yaml_exit_64(self, run_cli_real, monkeypatch, tmp_path) -> None:
        """YAML 语法错 → 退出码 64，错误指向配置文件。"""
        _write_config(tmp_path, "model: [unclosed\n")
        monkeypatch.chdir(tmp_path)
        code, _, err = run_cli_real("check", "--diff-file", "x.diff")
        assert code == 64
        assert CONFIG_FILE_NAME in err

    def test_field_type_error_exit_64(self, run_cli_real, monkeypatch, tmp_path) -> None:
        """字段类型错 → 退出码 64，错误指向字段。"""
        _write_config(tmp_path, "experts:\n  enabled: architecture\n")
        monkeypatch.chdir(tmp_path)
        code, _, err = run_cli_real("check", "--diff-file", "x.diff")
        assert code == 64
        assert "experts.enabled" in err

    def test_missing_key_exit_64_names_env_var(
        self, run_cli_real, monkeypatch, tmp_path, diff_file
    ) -> None:
        """构造期缺 key = 配置错误 → 64（区别于运行期模型失败 → 70）。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        code, _, err = run_cli_real("check", "--diff-file", str(diff_file))
        assert code == 64
        assert "DEEPSEEK_API_KEY" in err

    def test_custom_api_key_env_via_config_exit_64_names_it(
        self, run_cli_real, monkeypatch, tmp_path, diff_file
    ) -> None:
        """api_key_env 自定义名经配置生效：缺的是配置指定的那个变量。"""
        _write_config(tmp_path, "model:\n  api_key_env: TEAM_REVIEWER_KEY\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TEAM_REVIEWER_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # 缺省名不被回退使用
        code, _, err = run_cli_real("check", "--diff-file", str(diff_file))
        assert code == 64
        assert "TEAM_REVIEWER_KEY" in err
        assert "DEEPSEEK_API_KEY" not in err


class TestModeResolutionChain:
    """mode 四层优先级在 CLI 缝上的两个关键用例。"""

    def test_subcommand_default_beats_config_mode(
        self, run_cli, monkeypatch, tmp_path, diff_file, findings_json
    ) -> None:
        """子命令默认压过 config.mode：配置写 mentor，check 仍 gatekeeper。"""
        _write_config(tmp_path, "mode: mentor\n")
        monkeypatch.chdir(tmp_path)
        provider = ScriptedProvider([findings_json([])])
        code, out, _, _ = run_cli(
            provider, "check", "--diff-file", str(diff_file), "--json"
        )
        assert code == 0
        assert json.loads(out)["mode"] == "gatekeeper"

    def test_explicit_mode_beats_everything(
        self, run_cli, monkeypatch, tmp_path, diff_file, findings_json
    ) -> None:
        """--mode 压过一切：配置写 gatekeeper，--mode mentor 仍生效。"""
        _write_config(tmp_path, "mode: gatekeeper\n")
        monkeypatch.chdir(tmp_path)
        provider = ScriptedProvider([findings_json([])])
        code, out, _, _ = run_cli(
            provider,
            "precheck",
            "--diff-file",
            str(diff_file),
            "--mode",
            "mentor",
            "--json",
        )
        assert code == 0
        assert json.loads(out)["mode"] == "mentor"
