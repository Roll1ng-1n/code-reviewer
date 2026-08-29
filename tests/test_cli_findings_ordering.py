"""findings 确定性排序（文件, 严重度权重, 行号）与统一编号 F001..。"""

from __future__ import annotations

import json

from reviewer.model import ScriptedProvider


def _f(file: str, line: int, severity: str, message: str) -> dict:
    return {"file": file, "line": line, "severity": severity, "category": "logic", "message": message}


def test_sorted_by_file_severity_line(run_cli, diff_file, findings_json) -> None:
    """乱序输入 → (file, severity, line) 确定性输出。"""
    findings = [
        _f("b.py", 1, "nit", "b1"),
        _f("a.py", 5, "nit", "a5"),
        _f("a.py", 2, "blocker", "a2-blocker"),
        _f("a.py", 2, "concern", "a2-concern"),
    ]
    provider = ScriptedProvider([findings_json(findings)])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file), "--json")
    assert code == 1  # 含 blocker
    report = json.loads(out)
    order = [(f["file"], f["severity"], f["line"]) for f in report["findings"]]
    assert order == [
        ("a.py", "blocker", 2),
        ("a.py", "concern", 2),
        ("a.py", "nit", 5),
        ("b.py", "nit", 1),
    ]


def test_uniform_numbering_f001_onwards(run_cli, diff_file, findings_json) -> None:
    """排序之后统一编号，从 F001 起连续递增。"""
    findings = [_f(f"f{i}.py", 1, "nit", f"m{i}") for i in range(3)]
    provider = ScriptedProvider([findings_json(findings)])
    code, out, err, _ = run_cli(provider, "check", "--diff-file", str(diff_file), "--json")
    assert code == 0
    report = json.loads(out)
    assert [f["id"] for f in report["findings"]] == ["F001", "F002", "F003"]
