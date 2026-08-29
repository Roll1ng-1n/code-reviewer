"""--mode 默认与覆盖：precheck 默认 mentor、check 默认 gatekeeper，--mode 可显式覆盖。"""

from __future__ import annotations

import json


def _run_json(run_cli, diff_file, panel, *argv: str) -> tuple[int, dict]:
    code, out, err, _ = run_cli(panel(), *argv, "--json")
    return code, json.loads(out)


def test_precheck_defaults_mentor(run_cli, diff_file, panel) -> None:
    """precheck 不带 --mode → mentor。"""
    code, report = _run_json(run_cli, diff_file, panel, "precheck", "--diff-file", str(diff_file))
    assert code == 0
    assert report["mode"] == "mentor"


def test_check_defaults_gatekeeper(run_cli, diff_file, panel) -> None:
    """check 不带 --mode → gatekeeper。"""
    code, report = _run_json(run_cli, diff_file, panel, "check", "--diff-file", str(diff_file))
    assert code == 0
    assert report["mode"] == "gatekeeper"


def test_precheck_mode_override(run_cli, diff_file, panel) -> None:
    """precheck --mode gatekeeper 覆盖默认。"""
    code, report = _run_json(
        run_cli, diff_file, panel,
        "precheck", "--diff-file", str(diff_file), "--mode", "gatekeeper",
    )
    assert code == 0
    assert report["mode"] == "gatekeeper"


def test_check_mode_override(run_cli, diff_file, panel) -> None:
    """check --mode mentor 覆盖默认。"""
    code, report = _run_json(
        run_cli, diff_file, panel,
        "check", "--diff-file", str(diff_file), "--mode", "mentor",
    )
    assert code == 0
    assert report["mode"] == "mentor"
