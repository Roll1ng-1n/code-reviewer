"""Spec #12 story 14: piped UTF-8 Replay input through the CLI seam."""
import io
import json

import pytest

from reviewer import cli


@pytest.mark.parametrize("command", ["check", "precheck"])
def test_stdin_replay_matches_file(command, run_cli, panel, diff_file, monkeypatch, tmp_path):
    diff = diff_file.read_text(encoding="utf-8")
    code, out, _, file_provider = run_cli(panel(), command, "--diff-file", str(diff_file), "--repo", str(tmp_path), "--description", "意图", "--json")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(diff))
    stdin_code, stdin_out, _, stdin_provider = run_cli(panel(), command, "--diff-file", "-", "--repo", str(tmp_path), "--description", "意图", "--json")
    assert stdin_code == code == 0
    assert sorted(stdin_provider.calls) == sorted(file_provider.calls)
    report = json.loads(stdin_out)
    assert report["metadata"]["base_ref"] is report["metadata"]["head_ref"] is None
    assert report["summary"] == json.loads(out)["summary"]


def test_invalid_utf8_stdin_fails_before_model(run_cli, panel, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff"), encoding="ascii"))
    code, out, err, _ = run_cli(panel(), "check", "--diff-file", "-", "--repo", str(tmp_path), "--json")
    assert code == 64 and not out and not run_cli.configs
    assert "UTF-8" in err
    assert "decode" in err


def test_chinese_stdin_is_decoded_as_utf8(run_cli, panel, monkeypatch, tmp_path):
    diff = "+++ b/中文.py\n+中文代码\n"
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(diff.encode("utf-8")), encoding="ascii"))
    code, _, _, provider = run_cli(panel(), "check", "--diff-file", "-", "--repo", str(tmp_path), "--json")
    assert code == 0
    assert all("中文代码" in user for _, user in provider.calls)


def test_empty_stdin_short_circuits(run_cli, panel, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    code, out, _, provider = run_cli(panel(), "check", "--diff-file", "-", "--repo", str(tmp_path), "--json")
    assert code == 0 and not provider.calls and not run_cli.configs
    assert json.loads(out)["metadata"]["no_changes"] is True
