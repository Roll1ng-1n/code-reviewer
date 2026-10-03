"""Golden-set infrastructure failures and recipe isolation (#32, #34)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "eval/golden-set/compare_product.py"
spec = importlib.util.spec_from_file_location("compare_product", MODULE_PATH)
evaluation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluation
spec.loader.exec_module(evaluation)


def valid_report(model="deepseek-chat"):
    return {"schema_version": "1", "mode": "gatekeeper",
            "summary": {"verdict": "pass", "headline": "clean", "counts": {"blocker": 0, "concern": 0, "nit": 0}},
            "findings": [], "metadata": {"model": model, "repo": "snapshot", "base_ref": None,
                "head_ref": None, "duration_ms": 0, "timestamp": "2026-10-04T00:00:00+00:00",
                "spec_kb": {"loaded": False, "documents": 0, "hash": None},
                "context_stats": {"structure_map_lines": 0, "neighborhood_files": 0}}}


@pytest.mark.parametrize("failure", [64, 69, 70, 130, "json", "missing", "invalid", "timeout", "model", "judge", "counts", "mode", "metadata", "contaminated"])
def test_failed_round_does_not_score_or_overwrite(failure, monkeypatch, tmp_path, capsys):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "results").mkdir()
    result = assets / "results/compare-product-001.json"
    result.write_text("previous valid result", encoding="utf-8")
    (assets / "golden.json").write_text(json.dumps({"cases": [{"id": "CLEAN", "clean": True, "golden": []}]}), encoding="utf-8")
    monkeypatch.setattr(evaluation, "BASE", assets)
    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), "--runs", "1"])
    report = valid_report("wrong-model" if failure == "model" else "deepseek-chat")
    if failure == "invalid":
        report["findings"] = [None]
    elif failure == "counts":
        report["summary"]["counts"]["blocker"] = False
    elif failure == "mode":
        report["mode"] = "mentor"
    elif failure == "metadata":
        report["metadata"] = {}
    elif failure == "contaminated":
        report["metadata"]["context_stats"]["structure_map_lines"] = 10
    def fake_run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 600)
        return subprocess.CompletedProcess(args[0], failure if isinstance(failure, int) else 0,
                    "bad JSON" if failure == "json" else json.dumps({} if failure == "missing" else report), "model failed")
    monkeypatch.setattr(evaluation.subprocess, "run", fake_run)
    if failure == "judge":
        def failed_match(*args, **kwargs):
            raise ValueError("judge invalid JSON")
        monkeypatch.setattr(evaluation, "match", failed_match)
    assert evaluation.main() != 0
    out, err = capsys.readouterr()
    assert "CLEAN" in err and "panel" in err and "1" in err
    assert "=== 配对差异 ===" not in out
    assert result.read_text(encoding="utf-8") == "previous valid result"


def test_valid_pass_and_isolated_recipe(monkeypatch, tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / ".reviewer.yaml").write_text("model: {name: host-model}", encoding="utf-8")
    (host / "AGENTS.md").write_text("HOST_ROLES", encoding="utf-8")
    monkeypatch.chdir(host)
    captured = []
    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs["cwd"])
        repo = Path(cmd[cmd.index("--repo") + 1])
        assert cwd != host and repo.is_dir() and not list(repo.iterdir())
        config = json.loads((cwd / ".reviewer.yaml").read_text(encoding="utf-8"))
        assert config["model"]["name"] == "deepseek-chat"
        assert config["spec_kb"]["paths"] == []
        captured.append(config)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(valid_report()), "")
    monkeypatch.setattr(evaluation.subprocess, "run", fake_run)
    assert evaluation.run_arm_case("panel", {"id": "CLEAN"}) == []
    (host / ".reviewer.yaml").write_bytes(b"\xff")
    assert evaluation.run_arm_case("baseline", {"id": "CLEAN"}) == []
    assert captured[0] == captured[1]


def test_success_records_actual_models_and_recipe(monkeypatch, tmp_path):
    (tmp_path / "golden.json").write_text(json.dumps({"cases": [{"id": "CLEAN", "clean": True, "golden": []}]}), encoding="utf-8")
    monkeypatch.setattr(evaluation, "BASE", tmp_path)
    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), "--runs", "1"])
    monkeypatch.setattr(evaluation.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(valid_report()), ""))
    assert evaluation.main() == 0
    result = json.loads((tmp_path / "results/compare-product-001.json").read_text(encoding="utf-8"))
    assert result["evaluated_model"] == "deepseek-chat"
    assert result["judge_model"] == "deepseek-chat"
    assert result["recipe"] == "diff+description"
    assert result["configuration"]["spec_kb"]["paths"] == []
    assert result["panel"]["fps"] == result["baseline"]["fps"] == [0]


@pytest.mark.parametrize("response", ["not json", "{}", '{"same_underlying_issue": "false"}'])
def test_invalid_judge_response_is_failure(response, monkeypatch):
    monkeypatch.setattr(evaluation, "llm", lambda *a, **k: response)
    golden = {"file": "a.py", "severity": "blocker", "category": "logic", "description": "bug"}
    with pytest.raises(Exception):
        evaluation.judge(golden, {"file": "a.py"})


def test_recipe_exercises_real_cli_without_host_context(monkeypatch, tmp_path, run_cli, panel):
    host = tmp_path / "host"
    host.mkdir()
    (host / "AGENTS.md").write_text("HOST_ROLE_SENTINEL", encoding="utf-8")
    (host / "specs").mkdir()
    (host / "specs/rules.md").write_text("HOST_SPEC_SENTINEL", encoding="utf-8")
    (host / ".reviewer.yaml").write_text("model: {name: wrong-model}", encoding="utf-8")
    assets = tmp_path / "assets"
    (assets / "cases").mkdir(parents=True)
    (assets / "cases/S1.diff").write_text("+++ b/a.py\n+CASE_DIFF_SENTINEL\n", encoding="utf-8")
    (assets / "cases/S1.desc.txt").write_text("CASE_DESCRIPTION_SENTINEL", encoding="utf-8")
    monkeypatch.setattr(evaluation, "BASE", assets)
    monkeypatch.chdir(host)
    prompts = []
    def fake_run(cmd, **kwargs):
        from reviewer.model import ScriptedProvider
        provider = panel() if "panel" in cmd else ScriptedProvider(by_expert={"四维融合审查专家": '{"findings": []}'})
        provider.model_name = "deepseek-chat"
        with monkeypatch.context() as local:
            local.chdir(kwargs["cwd"])
            code, out, err, provider = run_cli(provider, *cmd[3:])
        assert all("HOST_ROLE_SENTINEL" not in u and "HOST_SPEC_SENTINEL" not in u for _, u in provider.calls)
        assert all("CASE_DIFF_SENTINEL" in u and "CASE_DESCRIPTION_SENTINEL" in u for _, u in provider.calls)
        assert run_cli.configs[-1].model.name == "deepseek-chat"
        prompts.append({u for _, u in provider.calls})
        return subprocess.CompletedProcess(cmd, code, out, err)
    monkeypatch.setattr(evaluation.subprocess, "run", fake_run)
    assert evaluation.run_arm_case("panel", {"id": "S1"}) == []
    (host / ".reviewer.yaml").write_bytes(b"\xff")
    assert evaluation.run_arm_case("baseline", {"id": "S1"}) == []
    assert prompts[0] == prompts[1]
