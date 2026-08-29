"""--json Report 五段契约（schema-v1）：schema_version / mode / summary / findings / metadata。"""

from __future__ import annotations

import json

from reviewer.contract import SCHEMA_VERSION


def test_report_five_section_contract(run_cli, diff_file, panel, make_finding) -> None:
    """完整 Report 契约断言：五段结构、summary 三键、findings 行级锚定、metadata 字段。"""
    finding = make_finding(
        severity="concern",
        file="src/calc.py",
        line=7,
        message="边界未处理",
        rationale="a + b 对 None 无防护",
        suggestion="加参数校验",
    )
    provider = panel(logic=[finding])
    code, out, err, _ = run_cli(
        provider,
        "check",
        "--diff-file", str(diff_file),
        "--description", "修复加法",
        "--repo", str(diff_file.parent),
        "--json",
    )
    assert code == 2  # concern → concerns
    report = json.loads(out)

    # 五段结构
    assert set(report) == {"schema_version", "mode", "summary", "findings", "metadata"}
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["mode"] == "gatekeeper"

    # summary：verdict 程序化派生 + 三级 counts
    summary = report["summary"]
    assert set(summary) == {"verdict", "headline", "counts"}
    assert summary["verdict"] == "concerns"
    assert summary["counts"] == {"blocker": 0, "concern": 1, "nit": 0}

    # findings：统一编号 + 行级锚定字段
    (row,) = report["findings"]
    assert row["id"] == "F001"
    assert row["file"] == "src/calc.py"
    assert row["line"] == 7
    assert row["severity"] == "concern"
    assert row["category"] == "logic"
    assert row["message"] == "边界未处理"
    assert row["rationale"] == "a + b 对 None 无防护"
    assert row["suggestion"] == "加参数校验"

    # metadata
    metadata = report["metadata"]
    assert set(metadata) == {
        "repo", "base_ref", "head_ref", "model", "spec_kb", "duration_ms", "timestamp",
        "description_source",  # #16：描述来源标注（additive，不升 schema_version）
        "context_stats",  # #17：上下文组装预算统计（additive，不升 schema_version）
    }
    assert metadata["model"] == "scripted-fake"
    assert metadata["repo"] == diff_file.parent.name
    assert metadata["base_ref"] is None
    assert metadata["head_ref"] is None
    assert metadata["description_source"] == "explicit"  # 上面显式传了 --description
    assert metadata["spec_kb"] == {"loaded": False, "documents": 0, "hash": None}
    assert isinstance(metadata["duration_ms"], int)
    assert "+00:00" in metadata["timestamp"]
    # #17：context_stats 全键可观测（回放 --repo 指向 tmp：被改 src/calc.py 不在快照 → 邻域 0）
    stats = metadata["context_stats"]
    assert set(stats) == {
        "structure_map_lines", "structure_map_truncated",
        "changed_py_files", "unresolved_files",
        "neighborhood_files", "neighborhood_tokens", "neighborhood_truncated",
    }
    assert stats["changed_py_files"] == 1
    assert stats["unresolved_files"] == 1
    assert stats["neighborhood_files"] == 0
    assert stats["neighborhood_tokens"] == 0
    assert stats["structure_map_lines"] > 0
    assert stats["structure_map_truncated"] is False
    assert stats["neighborhood_truncated"] is False


def test_optional_suggestion_omitted_when_absent(run_cli, diff_file, panel, make_finding) -> None:
    """suggestion 为空时输出不含该键（exclude_none 语义，字段可选）。"""
    provider = panel(logic=[make_finding()])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file), "--json")
    assert code == 0
    report = json.loads(out)
    (row,) = report["findings"]
    assert "suggestion" not in row


def test_json_and_human_render_share_one_report(run_cli, diff_file, panel, make_finding) -> None:
    """同一 Report 数据两种输出：--json 与人类可读渲染不漂移（user story 13）。"""
    finding = make_finding(severity="blocker", file="x.py", line=3, message="空指针")
    provider = panel(logic=[finding])
    code_json, out_json, _, _ = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--json"
    )
    provider2 = panel(logic=[finding])
    code_text, out_text, _, _ = run_cli(
        provider2, "check", "--diff-file", str(diff_file)
    )
    assert code_json == code_text == 1
    report = json.loads(out_json)
    assert report["findings"][0]["id"] == "F001"
    assert "F001" in out_text and "空指针" in out_text
