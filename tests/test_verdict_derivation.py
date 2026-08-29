"""verdict 程序化派生与退出码映射（contract 层单元测试）。"""

from __future__ import annotations

import pytest

from reviewer.contract import EXIT_CODES, derive_verdict, exit_code_for


def test_blocker_yields_blocked() -> None:
    """blocker≥1 → blocked（含 counts 与 headline）。"""
    summary = derive_verdict(
        [
            {"file": "a.py", "line": 1, "severity": "blocker", "category": "logic", "message": "m"},
            {"file": "b.py", "line": 2, "severity": "nit", "category": "logic", "message": "m"},
        ]
    )
    assert summary["verdict"] == "blocked"
    assert summary["counts"] == {"blocker": 1, "concern": 0, "nit": 1}


def test_concern_only_yields_concerns() -> None:
    """无 blocker 但有 concern → concerns。"""
    summary = derive_verdict(
        [{"file": "a.py", "line": 1, "severity": "concern", "category": "logic", "message": "m"}]
    )
    assert summary["verdict"] == "concerns"


def test_no_findings_yields_pass() -> None:
    """零发现 → pass。"""
    summary = derive_verdict([])
    assert summary["verdict"] == "pass"
    assert summary["counts"] == {"blocker": 0, "concern": 0, "nit": 0}
    assert summary["headline"] == "未发现 blocker 或 concern"


def test_exit_code_mapping() -> None:
    """三值退出码契约：pass→0 / blocked→1 / concerns→2。"""
    assert EXIT_CODES == {"pass": 0, "blocked": 1, "concerns": 2}
    for verdict, code in EXIT_CODES.items():
        assert exit_code_for(verdict) == code
