"""Shared context, dimensions and style guards for both Review Arms (#33)."""
import json

import pytest

from reviewer.model import ScriptedProvider


@pytest.mark.parametrize("enabled", [["logic", "spec", "style"], ["logic"], ["style"]])
def test_arms_share_enabled_dimensions_and_spec(enabled, run_cli, panel, diff_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".reviewer.yaml").write_text("experts:\n  enabled: " + json.dumps(enabled), encoding="utf-8")
    spec = tmp_path / "orders.md"
    spec.write_text("# Order rules\n## Refunds\nREFUND_ONCE\n", encoding="utf-8")
    reports = []
    prompts = []
    for arm in ("panel", "baseline"):
        provider = panel() if arm == "panel" else ScriptedProvider(by_expert={"四维融合审查专家": '{"findings": []}'})
        code, out, _, provider = run_cli(provider, "check", "--arm", arm, "--diff-file", str(diff_file), "--repo", str(tmp_path), "--spec", str(spec), "--json")
        assert code == 0
        reports.append(json.loads(out))
        prompts.append(provider.calls)
    assert reports[0]["metadata"]["enabled_experts"] == reports[1]["metadata"]["enabled_experts"] == enabled
    fusion_system, fusion_user = prompts[1][0]
    assert "本仓库无成文规范库" not in fusion_system
    assert ("REFUND_ONCE" in fusion_user) == ("spec" in enabled)
    if "spec" in enabled:
        spec_user = next(u for s, u in prompts[0] if "「spec」" in s)
        assert spec_user == fusion_user
    for category in ("architecture", "logic", "spec", "style"):
        assert (f"（{category}）：" in fusion_system) == (category in enabled)


def test_baseline_empty_kb_disables_spec(run_cli, panel, diff_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider(by_expert={"四维融合审查专家": '{"findings": []}'})
    code, out, _, provider = run_cli(provider, "check", "--arm", "baseline", "--diff-file", str(diff_file), "--repo", str(tmp_path), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["metadata"]["spec_kb"]["loaded"] is False
    assert "spec" not in report["metadata"]["enabled_experts"]
    assert "## Spec KB" not in provider.calls[0][1]


def test_baseline_style_guard_preserves_other_categories(run_cli, diff_file, tmp_path, monkeypatch, make_finding):
    monkeypatch.chdir(tmp_path)
    findings = [make_finding(file=f"style{i}.py", category="style", message=f"style {i}") for i in range(5)]
    findings += [make_finding(file="badstyle.py", category="style", severity="blocker"),
                 make_finding(file="logic.py", category="logic", severity="blocker")]
    def respond(system, user):
        if "四维融合审查专家" in system:
            return json.dumps({"findings": findings})
        return json.dumps({"findings": json.loads(user.split("【findings】")[1])})
    code, out, _, _ = run_cli(ScriptedProvider(callback=respond), "check", "--arm", "baseline", "--diff-file", str(diff_file), "--repo", str(tmp_path), "--json")
    assert code == 1
    rows = json.loads(out)["findings"]
    styles = [f for f in rows if f["category"] == "style"]
    assert len(styles) == 3 and all(f["severity"] == "nit" for f in styles)
    assert any(f["category"] == "logic" and f["severity"] == "blocker" for f in rows)
