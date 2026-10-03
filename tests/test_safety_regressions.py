"""Issue #24–#31 regressions through the zero-network CLI seam."""
import json
import hashlib

import pytest
from langgraph.checkpoint.memory import MemorySaver

from reviewer import cli
from reviewer.model import ScriptedProvider


def provider_for(findings, *, review=None, merge=None):
    def respond(system, user):
        if "复核过滤器" in system:
            candidates = json.loads(user.split("【findings】", 1)[1])
            return json.dumps({"findings": review(candidates) if review else candidates})
        if "合并去重器" in system:
            candidates = json.loads(user.split("【findings】", 1)[1])
            return json.dumps({"findings": merge(candidates) if merge else candidates})
        return json.dumps({"findings": findings if "「logic」" in system else []})
    return ScriptedProvider(callback=respond)


def test_review_preserves_distinct_same_message(run_cli, diff_file, make_finding):
    rows = [make_finding(line=1, message="Missing guard"),
            make_finding(line=20, severity="blocker", message="Missing guard")]
    code, out, _, _ = run_cli(provider_for(rows), "check", "--diff-file", str(diff_file), "--json")
    assert code == 1
    assert {(f["line"], f["severity"]) for f in json.loads(out)["findings"]} == {(1, "nit"), (20, "blocker")}


@pytest.mark.parametrize("fault", ["rewrite", "unknown", "duplicate", "missing", "nonobject"])
def test_bad_review_conservatively_keeps_all(fault, run_cli, diff_file, make_finding):
    rows = [make_finding(severity="blocker", message="Refund duplicated"),
            make_finding(file="b.py", message="Naming improvement")]
    def review(candidates):
        if fault == "rewrite":
            return [{**candidates[0], "message": "Duplicate refund detected"}, candidates[1]]
        if fault == "unknown":
            return [{**candidates[0], "id": "unknown"}, candidates[1]]
        if fault == "duplicate":
            return [candidates[1], candidates[1]]
        if fault == "missing":
            return [{"message": "Naming improvement"}]
        return [None, candidates[1]]
    code, out, err, _ = run_cli(provider_for(rows, review=review), "check", "--diff-file", str(diff_file), "--json")
    assert code == 1
    assert len(json.loads(out)["findings"]) == 2
    assert "保守保留全部" in err


def test_valid_review_uses_candidate_identity(run_cli, diff_file, make_finding):
    rows = [make_finding(line=1, message="same"), make_finding(line=20, severity="blocker", message="same")]
    code, out, _, _ = run_cli(provider_for(rows, review=lambda cs: [{"id": cs[1]["id"]}]), "check", "--diff-file", str(diff_file), "--json")
    assert code == 1
    assert [f["line"] for f in json.loads(out)["findings"]] == [20]


@pytest.mark.parametrize("severities", [("concern", "blocker", "nit"), ("nit", "concern"), ("blocker", "concern")])
def test_merge_does_not_upgrade_independent_findings(severities, run_cli, diff_file, make_finding):
    rows = [make_finding(line=10+i, severity=sev, message=f"independent {i}") for i, sev in enumerate(severities)]
    code, out, _, _ = run_cli(provider_for(rows), "check", "--diff-file", str(diff_file), "--json")
    actual = json.loads(out)
    assert {f["message"]: f["severity"] for f in actual["findings"]} == {f["message"]: f["severity"] for f in rows}
    assert code == (1 if "blocker" in severities else 2)


@pytest.mark.parametrize("decision", ["0", "-1", "999", "999,x", "1,x", "1,,2", ""])
def test_invalid_confirmation_keeps_blocker(decision, monkeypatch, run_cli, diff_file, make_finding):
    monkeypatch.setattr(cli, "_make_checkpointer", MemorySaver)
    monkeypatch.setattr(cli, "input", lambda: decision, raising=False)
    code, out, _, _ = run_cli(provider_for([make_finding(severity="blocker")]), "check", "--diff-file", str(diff_file), "--interactive", "--json")
    assert code == 1
    assert len(json.loads(out)["findings"]) == 1


def test_new_interactive_runs_reconfirm(monkeypatch, run_cli, diff_file, make_finding):
    saver = MemorySaver()
    monkeypatch.setattr(cli, "_make_checkpointer", lambda: saver)
    answers = iter(["d", "a"])
    monkeypatch.setattr(cli, "input", lambda: next(answers), raising=False)
    for message, expected in [("first", 0), ("second", 1)]:
        code, out, _, provider = run_cli(provider_for([make_finding(severity="blocker", message=message)]), "check", "--diff-file", str(diff_file), "--interactive", "--json")
        assert code == expected
        assert [f["message"] for f in json.loads(out)["findings"]] == ([] if expected == 0 else [message])
        assert len([s for s, _ in provider.calls if "「logic」" in s]) == 1


@pytest.mark.parametrize("kind", ["config", "diff", "description"])
def test_invalid_utf8_is_usage_error(kind, monkeypatch, run_cli, panel, diff_file, tmp_path):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / (".reviewer.yaml" if kind == "config" else "invalid.txt")
    bad.write_bytes(b"\xff")
    argv = ["check", "--diff-file", str(bad if kind == "diff" else diff_file), "--json"]
    if kind == "description":
        argv.extend(["--description-file", str(bad)])
    code, out, err, provider = run_cli(panel(), *argv)
    assert code == 64
    assert not out and not provider.calls
    assert not run_cli.configs
    assert str(bad) in err and "UTF-8" in err and "Traceback" not in err


@pytest.mark.parametrize("final_content,expected", [("content A", 2), ("content B", 1)])
def test_spec_override_deduplicates_final_content(final_content, expected, run_cli, panel, diff_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/orders.md").write_text("content A", encoding="utf-8")
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra/orders.md").write_text("content B", encoding="utf-8")
    (tmp_path / "extra/refunds.md").write_text(final_content, encoding="utf-8")
    code, out, _, provider = run_cli(panel(), "check", "--diff-file", str(diff_file), "--repo", str(tmp_path), "--spec", "extra", "--json")
    assert code == 0
    assert json.loads(out)["metadata"]["spec_kb"]["documents"] == expected
    content = "content B" + ("content A" if expected == 2 else "")
    assert json.loads(out)["metadata"]["spec_kb"]["hash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    prompt = next(u for s, u in provider.calls if "「spec」" in s)
    assert prompt.count("content B") == 1
    assert ("content A" in prompt) == (expected == 2)


@pytest.mark.parametrize("decision", ["1,3", "1 3", "1,1,3"])
def test_valid_confirmation_precisely_selects(decision, monkeypatch, run_cli, diff_file, make_finding):
    monkeypatch.setattr(cli, "_make_checkpointer", MemorySaver)
    monkeypatch.setattr(cli, "input", lambda: decision, raising=False)
    rows = [make_finding(file=f"{i}.py", message=str(i)) for i in range(1, 4)]
    code, out, _, _ = run_cli(provider_for(rows), "check", "--diff-file", str(diff_file), "--interactive", "--json")
    assert code == 0
    assert [f["message"] for f in json.loads(out)["findings"]] == ["1", "3"]


@pytest.mark.parametrize("decision", ["q", "EOF"])
def test_confirmation_abort_has_no_report(decision, monkeypatch, run_cli, diff_file, make_finding):
    monkeypatch.setattr(cli, "_make_checkpointer", MemorySaver)
    def answer():
        if decision == "EOF":
            raise EOFError()
        return decision
    monkeypatch.setattr(cli, "input", answer, raising=False)
    code, out, _, _ = run_cli(provider_for([make_finding(severity="blocker")]), "check", "--diff-file", str(diff_file), "--interactive", "--json")
    assert code == 130 and not out


@pytest.mark.parametrize("original,returned,expected", [("blocker", "nit", "nit"), ("concern", "blocker", "concern")])
def test_merge_caps_only_actual_upgrades(original, returned, expected, run_cli, diff_file, make_finding):
    rows = [make_finding(line=10, severity=original), make_finding(line=12)]
    def merge(cs):
        return [{**cs[0], "severity": returned}]
    code, out, _, _ = run_cli(provider_for(rows, merge=merge), "check", "--diff-file", str(diff_file), "--json")
    assert [f["severity"] for f in json.loads(out)["findings"]] == [expected]
    assert code == (0 if expected == "nit" else 2)


def test_config_io_error_is_usage_error(run_cli, panel, diff_file, monkeypatch, tmp_path):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    read_text = Path.read_text
    def read(path, *args, **kwargs):
        if path.name == ".reviewer.yaml":
            raise PermissionError("denied")
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    code, out, err, _ = run_cli(panel(), "check", "--diff-file", str(diff_file), "--json")
    assert code == 64 and not out and not run_cli.configs
    assert ".reviewer.yaml" in err and "denied" in err


def test_valid_chinese_utf8_inputs(run_cli, panel, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".reviewer.yaml").write_text("base: 中文分支\n", encoding="utf-8")
    diff = tmp_path / "中文.diff"
    diff.write_text("+++ b/中文.py\n+中文内容\n", encoding="utf-8")
    desc = tmp_path / "描述.txt"
    desc.write_text("中文意图", encoding="utf-8")
    code, _, _, provider = run_cli(panel(), "check", "--diff-file", str(diff), "--description-file", str(desc), "--json")
    assert code == 0
    assert run_cli.configs[0].base == "中文分支"
    assert all("中文内容" in u and "中文意图" in u for _, u in provider.calls)
