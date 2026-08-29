"""渲染层双模式分化（#21）单元测试：gatekeeper 折叠 nit、mentor 导师式展开。

覆盖验收标准：
* Gatekeeper：verdict 头部 + 按严重度分组（blocker→concern→nit）+ nit 默认折叠（有展开入口）
* Mentor：逐条展开 rationale + suggestion + 教学语气（面向作者）
* 两种渲染消费同一 Report JSON；--json 输出不受渲染影响
* 数据契约无渲染字段（渲染关注点不渗入 schema）
"""

from __future__ import annotations

import json

from reviewer.contract import Finding
from reviewer.render import render_human


def _report(mode: str, findings: list[dict]) -> dict:
    """构造含 blocker/concern/nit 三级严重度的 Report（共享数据契约形状）。"""
    blocker = sum(1 for f in findings if f["severity"] == "blocker")
    concern = sum(1 for f in findings if f["severity"] == "concern")
    nit = sum(1 for f in findings if f["severity"] == "nit")
    verdict = "blocked" if blocker else ("concerns" if concern else "pass")
    headline = "blocked" if blocker else "concerns" if concern else "pass"
    return {
        "schema_version": "1",
        "mode": mode,
        "summary": {
            "verdict": verdict,
            "headline": headline,
            "counts": {"blocker": blocker, "concern": concern, "nit": nit},
        },
        "findings": findings,
        "metadata": {},
    }


def _mixed_findings() -> list[dict]:
    return [
        {"id": "F001", "file": "a.py", "line": 2, "severity": "blocker",
         "category": "architecture", "message": "跨模块越界",
         "rationale": "依赖方向倒置", "suggestion": "抽出接口"},
        {"id": "F002", "file": "a.py", "line": 5, "severity": "concern",
         "category": "logic", "message": "边界未处理",
         "rationale": "a+b 对 None 无防护", "suggestion": "加校验"},
        {"id": "F003", "file": "b.py", "line": 1, "severity": "nit",
         "category": "style", "message": "命名可读性",
         "rationale": "x 含义不明", "suggestion": "改名 total"},
    ]


def test_gatekeeper_groups_by_severity_and_folds_nit() -> None:
    """gatekeeper：blocker/concern 逐条完整，nit 默认折叠为汇总行（不出现 message）。"""
    out = render_human(_report("gatekeeper", _mixed_findings()))
    assert "verdict: blocked" in out
    assert "headline:" in out
    # blocker/concern 逐条完整（message 出现）
    assert "跨模块越界" in out
    assert "边界未处理" in out
    # nit 默认折叠：message 不在输出，汇总行含计数与展开入口
    assert "命名可读性" not in out
    assert "nit × 1" in out
    assert "--show-nits" in out


def test_gatekeeper_show_nits_expands() -> None:
    """gatekeeper --show-nits：nit 的 message 出现（展开）。"""
    folded = render_human(_report("gatekeeper", _mixed_findings()))
    assert "命名可读性" not in folded
    expanded = render_human(_report("gatekeeper", _mixed_findings()), show_nits=True)
    assert "命名可读性" in expanded


def test_gatekeeper_severity_group_order() -> None:
    """gatekeeper 组序 blocker → concern → nit；组内按 file:line。"""
    out = render_human(_report("gatekeeper", _mixed_findings()))
    assert out.index("跨模块越界") < out.index("边界未处理")
    assert out.index("边界未处理") < out.index("nit × 1")


def test_mentor_expands_every_finding_with_guidance() -> None:
    """mentor：逐条展开，含「为什么」「建议」引导语；nit 不折叠；教学口吻开场。"""
    out = render_human(_report("mentor", _mixed_findings()))
    assert "提交前的预检" in out
    # 每条 rationale/suggestion 都有中文引导语
    assert "为什么：依赖方向倒置" in out
    assert "建议：抽出接口" in out
    assert "为什么：a+b 对 None 无防护" in out
    assert "建议：加校验" in out
    # nit 不折叠：message 出现 + 引导语
    assert "命名可读性" in out
    assert "为什么：x 含义不明" in out
    assert "建议：改名 total" in out
    # 无折叠汇总行
    assert "nit × 1" not in out


def test_both_modes_consume_same_report() -> None:
    """两种渲染消费同一 Report 数据（同一 findings 源）。"""
    report = _report("gatekeeper", _mixed_findings())
    gk = render_human(report)
    report["mode"] = "mentor"
    mt = render_human(report)
    # 同一 findings 三 message 在 mentor 全出现（gatekeeper 折叠 nit）
    for msg in ("跨模块越界", "边界未处理", "命名可读性"):
        assert msg in mt
    assert "跨模块越界" in gk and "边界未处理" in gk


def test_empty_report_no_changes_vs_no_findings() -> None:
    """空报告区分：#16 no_changes 语义不得丢。"""
    no_changes = _report("gatekeeper", [])
    no_changes["metadata"]["no_changes"] = True
    assert "无改动" in render_human(no_changes)

    no_findings = _report("gatekeeper", [])
    assert "无发现" in render_human(no_findings)


def test_finding_serializes_without_render_fields() -> None:
    """数据契约无渲染字段：Finding 序列化不含 render/折叠类键。

    只断言「渲染关注点不渗入 schema」——不锁死 Finding 的完整字段集
    （rationale 默认空串也会序列化，字段集由契约票 #6 定，非本票关注点）。
    """
    finding = Finding(
        file="a.py", line=1, severity="nit", category="style", message="m"
    )
    dumped = finding.model_dump(exclude_none=True)
    for key in dumped:
        assert "render" not in key and "fold" not in key and "collapse" not in key
    assert "show_nits" not in dumped
    assert "grouped" not in dumped


def test_json_output_unaffected_by_show_nits(run_cli, diff_file, panel, make_finding) -> None:
    """--json 输出与渲染无关：--show-nits 不影响 JSON 字段。"""
    finding = make_finding(severity="nit", file="x.py", line=1, message="nit 信息")
    provider = panel(logic=[finding])
    code, out, err, _ = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--json", "--show-nits"
    )
    report = json.loads(out)
    # nit 仍完整存在（渲染折叠只影响人类可读文本，不影响 JSON 契约）
    assert report["findings"][0]["severity"] == "nit"
    assert report["findings"][0]["message"] == "nit 信息"
