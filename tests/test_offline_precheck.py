"""Precheck must stay offline unless the caller explicitly allows network use."""

import json
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def offline_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cloud-key-must-not-be-used")
    network_calls = []

    def network(*args, **kwargs):
        network_calls.append(args)
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr("urllib.request.urlopen", network)
    return network_calls


@pytest.fixture
def local_engine(monkeypatch, tmp_path, offline_env):
    (tmp_path / "model.gguf").write_bytes(b"fake GGUF test fixture")
    (tmp_path / ".reviewer.yaml").write_text(
        "precheck:\n  model:\n    name: test-local\n    path: model.gguf\n", encoding="utf-8"
    )
    instances = []

    class Engine:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.calls = []
            self.active = False
            self.closed = False
            instances.append(self)

        def create_chat_completion(self, **kwargs):
            assert not self.active, "Local engine must not receive concurrent calls"
            self.active = True
            time.sleep(0.005)
            self.calls.append(kwargs)
            self.active = False
            return {"choices": [{"message": {"content": '{"findings": []}'}}]}

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=Engine))
    return instances


@pytest.mark.parametrize("arm,call_count", [("panel", 3), ("baseline", 1)])
def test_precheck_uses_local_engine_for_both_arms(
    run_cli_real, local_engine, offline_env, diff_file, tmp_path, arm, call_count
):
    # --repo must not change the configuration-relative model path.
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    code, out, err = run_cli_real(
        "precheck", "--diff-file", str(diff_file), "--repo", str(snapshot),
        "--arm", arm, "--json",
    )
    assert code == 0, err
    report = json.loads(out)
    assert report["metadata"]["model"] == "test-local"
    assert report["mode"] == "mentor"
    engine, = local_engine
    assert engine.options["model_path"] == str((tmp_path / "model.gguf").resolve())
    assert len(engine.calls) == call_count
    assert all(call["temperature"] == 0 for call in engine.calls)
    assert all(call["response_format"] == {"type": "json_object"} for call in engine.calls)
    assert all([m["role"] for m in call["messages"]] == ["system", "user"] for call in engine.calls)
    assert "return a + b" in engine.calls[0]["messages"][1]["content"]
    assert engine.closed
    assert not offline_env


def test_unconfigured_precheck_fails_without_cloud_fallback(run_cli_real, offline_env, diff_file):
    code, out, err = run_cli_real("precheck", "--diff-file", str(diff_file))
    assert code == 64
    assert not out
    assert "precheck.model.path" in err
    assert not offline_env


@pytest.mark.parametrize("content,expected", [
    ("precheck:\n  model:\n    path: missing.gguf\n", "missing.gguf"),
    ("precheck:\n  model:\n    path: .\n", "本地模型文件"),
    ("precheck:\n  model:\n    path: 42\n", "precheck.model.path"),
    ("precheck:\n  model:\n    provider: deepseek\n", "--allow-network"),
])
def test_invalid_local_configuration_never_uses_network(
    run_cli_real, offline_env, diff_file, tmp_path, content, expected
):
    (tmp_path / ".reviewer.yaml").write_text(content, encoding="utf-8")
    code, out, err = run_cli_real("precheck", "--diff-file", str(diff_file))
    assert code == 64
    assert not out
    assert expected in err
    assert not offline_env


def test_missing_local_extra_reports_setup_error(
    run_cli_real, local_engine, offline_env, diff_file, monkeypatch
):
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    code, out, err = run_cli_real("precheck", "--diff-file", str(diff_file))
    assert code == 64
    assert not out
    assert "reviewer[local]" in err
    assert not offline_env


def test_failed_local_load_is_configuration_error(
    run_cli_real, local_engine, offline_env, diff_file, monkeypatch
):
    def bad_load(**kwargs):
        raise ValueError("invalid GGUF")

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=bad_load))
    code, out, err = run_cli_real("precheck", "--diff-file", str(diff_file))
    assert code == 64
    assert "invalid GGUF" in err
    assert not out
    assert not offline_env


def test_failed_local_inference_does_not_fall_back_to_cloud(
    run_cli_real, local_engine, offline_env, diff_file, monkeypatch
):
    engine_type = sys.modules["llama_cpp"].Llama

    def fail(self, **kwargs):
        raise RuntimeError("local inference failed")

    monkeypatch.setattr(engine_type, "create_chat_completion", fail)
    code, out, err = run_cli_real("precheck", "--diff-file", str(diff_file))
    assert code == 70
    assert not out
    assert "local inference failed" in err
    assert local_engine[0].closed
    assert not offline_env


def test_allow_network_explicitly_selects_cloud_model(
    run_cli_real, local_engine, offline_env, diff_file, monkeypatch
):
    calls = []

    def cloud(self, *, system, user):
        calls.append(self.model_name)
        return '{"findings": []}'

    monkeypatch.setattr("reviewer.model.DeepSeekProvider.complete", cloud)
    code, out, err = run_cli_real(
        "precheck", "--allow-network", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0, err
    assert json.loads(out)["metadata"]["model"] == "deepseek-chat"
    assert calls
    assert not local_engine


def test_empty_precheck_needs_no_model(run_cli_real, offline_env, tmp_path):
    empty = tmp_path / "empty.diff"
    empty.write_text("", encoding="utf-8")
    code, out, err = run_cli_real("precheck", "--diff-file", str(empty), "--json")
    assert code == 0, err
    assert json.loads(out)["metadata"]["no_changes"]
    assert not offline_env
