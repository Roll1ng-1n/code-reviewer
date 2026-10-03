"""Spec #12: product P/R, clean-PR rate, three-way classification and line signals."""
import json
import sys

import pytest

from test_product_evaluation import evaluation


def finding(file, message, line=10):
    return {"id": "F001", "file": file, "line": line, "category": "logic",
            "severity": "concern", "message": message, "rationale": "evidence"}


def write_assets(tmp_path, monkeypatch):
    cases = [{"id": "BUG", "clean": False, "golden": [
        {"id": "G1", "file": "a.py", "line": 10, "category": "logic", "severity": "concern", "description": "bug"}]},
        {"id": "CLEAN1", "clean": True, "golden": []},
        {"id": "CLEAN2", "clean": True, "golden": []}]
    (tmp_path / "cases").mkdir()
    for case in cases:
        (tmp_path / "cases" / f"{case['id']}.diff").write_text("+++ b/a.py\n+code\n", encoding="utf-8")
        (tmp_path / "cases" / f"{case['id']}.desc.txt").write_text("case description", encoding="utf-8")
    (tmp_path / "golden.json").write_text(json.dumps({"cases": cases}), encoding="utf-8")
    monkeypatch.setattr(evaluation, "BASE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["compare_product.py", "--runs", "3"])


def test_product_metrics_distinguish_unmatched_from_hallucinated(tmp_path, monkeypatch):
    write_assets(tmp_path, monkeypatch)
    def run(arm, case, **kwargs):
        if case["id"] == "BUG":
            return [finding("a.py", "bug", line=30 if arm == "baseline" else 12)]
        if case["id"] == "CLEAN1":
            return [finding("b.py", "plausible"), finding("c.py", "fabricated")]
        return []
    monkeypatch.setattr(evaluation, "run_arm_case", run)
    def judge(system, user):
        if "same_underlying_issue" in system:
            return '{"same_underlying_issue": true}'
        classification = "FABRICATED" if '"message": "fabricated"' in system else "PLAUSIBLE"
        return json.dumps({"classification": classification, "reason": "mock evidence"})
    monkeypatch.setattr(evaluation, "llm", judge)
    assert evaluation.main() == 0
    result = json.loads((tmp_path / "results/compare-product-001.json").read_text(encoding="utf-8"))
    for arm in ("panel", "baseline"):
        metrics = result[arm]
        assert metrics["precisions"] == [1 / 3] * 3
        assert metrics["recalls"] == [1.0] * 3
        assert metrics["clean_fp_counts"] == [2] * 3
        assert metrics["clean_fp_per_pr"] == [1.0] * 3
        assert metrics["clean_pr_false_positive_rates"] == [0.5] * 3
        assert metrics["hallucination_rates"] == [1 / 3] * 3
        assert metrics["classification_counts"] == [{"CONFIRMED": 1, "PLAUSIBLE": 1, "FABRICATED": 1}] * 3
    assert result["panel"]["line_signal_counts"] == [{"within_3": 1, "outside_3": 0, "unavailable": 0}] * 3
    assert result["baseline"]["line_signal_counts"] == [{"within_3": 0, "outside_3": 1, "unavailable": 0}] * 3
    assert result["panel"]["tps"] == result["baseline"]["tps"] == [1] * 3


@pytest.mark.parametrize("response", ["bad JSON", "{}", '{"classification":"FP","reason":"x"}', '{"classification":"CONFIRMED","reason":"x","golden_id":"missing"}'])
def test_classification_failure_aborts_without_overwriting(response, tmp_path, monkeypatch, capsys):
    write_assets(tmp_path, monkeypatch)
    (tmp_path / "results").mkdir()
    output = tmp_path / "results/compare-product-001.json"
    output.write_text("valid previous result", encoding="utf-8")
    monkeypatch.setattr(evaluation, "run_arm_case", lambda *a, **kw: [finding("unmatched.py", "question")])
    monkeypatch.setattr(evaluation, "llm", lambda *a: response)
    assert evaluation.main() == 1
    assert output.read_text(encoding="utf-8") == "valid previous result"
    assert "BUG" in capsys.readouterr().err


def test_line_signal_never_controls_semantic_match(monkeypatch):
    monkeypatch.setattr(evaluation, "judge", lambda *a: True)
    case = {"clean": False, "golden": [{"id": "G", "file": "a.py", "line": 10}]}
    details = {}
    assert evaluation.match(case, [finding("a.py", "bug", line=100)], details=details) == (1, 0, 0)
    assert details["matches"][0]["line_delta"] == 90
    assert details["matches"][0]["line_signal"] == "outside_3"
