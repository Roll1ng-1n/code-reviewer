"""--mode 默认与覆盖：precheck 默认 mentor、check 默认 gatekeeper，--mode 可显式覆盖。"""

from __future__ import annotations

import json

from reviewer.model import ScriptedProvider


def _run_json(run_cli, diff_file, findings_json, *argv: str) -> tuple[int, dict]:
    provider = ScriptedProvider([findings_json([])])
    code, out, err, _ = run_cli(provider, *argv, "--json")
    return code, json.loads(out)


def test_precheck_defaults_mentor(run_cli, diff_file, findings_json) -> None:
    """precheck 不带 --mode → mentor。"""
    code, report = _run_json(run_cli, diff_file, findings_json, "precheck", "--diff-file", str(diff_file))
    assert code == 0
    assert report["mode"] == "mentor"


def test_check_defaults_gatekeeper(run_cli, diff_file, findings_json) -> None:
    """check 不带 --mode → gatekeeper。"""
    code, report = _run_json(run_cli, diff_file, findings_json, "check", "--diff-file", str(diff_file))
    assert code == 0
    assert report["mode"] == "gatekeeper"


def test_precheck_mode_override(run_cli, diff_file, findings_json) -> None:
    """precheck --mode gatekeeper 覆盖默认。"""
    code, report = _run_json(
        run_cli, diff_file, findings_json,
        "precheck", "--diff-file", str(diff_file), "--mode", "gatekeeper",
    )
    assert code == 0
    assert report["mode"] == "gatekeeper"


def test_check_mode_override(run_cli, diff_file, findings_json) -> None:
    """check --mode mentor 覆盖默认。"""
    code, report = _run_json(
        run_cli, diff_file, findings_json,
        "check", "--diff-file", str(diff_file), "--mode", "mentor",
    )
    assert code == 0
    assert report["mode"] == "mentor"
