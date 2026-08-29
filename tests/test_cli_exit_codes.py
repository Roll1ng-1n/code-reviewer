"""CLI 缝退出码契约：verdict 三值（0/1/2）与用法错 64 / 未实现 69 / 运行时错 70。"""

from __future__ import annotations

import pytest

from reviewer import cli
from reviewer.model import ScriptedProvider


def test_pass_exit_zero(run_cli, diff_file, findings_json) -> None:
    """零发现 → verdict=pass → 0。"""
    provider = ScriptedProvider([findings_json([])])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
    assert code == 0


def test_concerns_exit_two(run_cli, diff_file, findings_json, make_finding) -> None:
    """仅 concern → verdict=concerns → 2。"""
    provider = ScriptedProvider([findings_json([make_finding(severity="concern")])])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
    assert code == 2


def test_blocked_exit_one(run_cli, diff_file, findings_json, make_finding) -> None:
    """有 blocker → verdict=blocked → 1。"""
    provider = ScriptedProvider([findings_json([make_finding(severity="blocker")])])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
    assert code == 1


def test_unknown_flag_exit_64() -> None:
    """未知参数 → argparse 自定义 error → SystemExit(64)（不与 concerns 撞码）。"""
    with pytest.raises(SystemExit) as ei:
        cli.main(["check", "--no-such-flag"])
    assert ei.value.code == 64


def test_invalid_mode_choice_exit_64() -> None:
    """--mode 非法取值 → 64。"""
    with pytest.raises(SystemExit) as ei:
        cli.main(["check", "--mode", "wrong"])
    assert ei.value.code == 64


def test_pr_command_exit_69(run_cli) -> None:
    """pr 命令面存在但未实现 → 69。"""
    code, out, err, _ = run_cli(ScriptedProvider(), "pr", "42")
    assert code == 69
    assert "预留" in err


def test_check_without_diff_file_exit_69(run_cli) -> None:
    """check 无 --diff-file（真实 git 输入未接入）→ 69。"""
    code, out, err, _ = run_cli(ScriptedProvider(), "check")
    assert code == 69
    assert "--diff-file" in err


def test_precheck_without_diff_file_exit_69(run_cli) -> None:
    """precheck 无 --diff-file → 69。"""
    code, out, err, _ = run_cli(ScriptedProvider(), "precheck")
    assert code == 69


def test_provider_failure_exit_70(run_cli, diff_file) -> None:
    """模型调用失败（provider 抛异常）→ ReviewerError → 70。"""

    def boom(system: str, user: str) -> str:
        raise RuntimeError("模拟模型调用失败")

    provider = ScriptedProvider(callback=boom)
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
    assert code == 70
    assert "评审运行失败" in err
