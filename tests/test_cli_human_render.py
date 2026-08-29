"""人类可读渲染冒烟（无 --json）：同一 Report 数据的文本形态。"""

from __future__ import annotations


def test_human_render_smoke(run_cli, diff_file, panel, make_finding) -> None:
    """人类可读输出包含模式、verdict、counts、编号与行级锚定。"""
    finding = make_finding(
        severity="blocker",
        file="src/x.py",
        line=3,
        message="空指针风险",
        rationale="未判空即解引用",
        suggestion="先判空",
    )
    provider = panel(logic=[finding])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file))
    assert code == 1
    assert "AI Code Reviewer" in out
    assert "gatekeeper mode" in out
    assert "verdict: blocked" in out
    assert "blocker=1" in out
    assert "F001" in out
    assert "src/x.py:3" in out
    assert "空指针风险" in out
    assert "why: 未判空即解引用" in out
    assert "fix: 先判空" in out


def test_human_render_empty_findings(run_cli, diff_file, panel) -> None:
    """零发现的人类可读输出：mentor 模式 + pass。"""
    provider = panel()
    code, out, err, _ = run_cli(provider, "precheck", "--diff-file", str(diff_file))
    assert code == 0
    assert "mentor mode" in out
    assert "verdict: pass" in out
    assert "无发现" in out
