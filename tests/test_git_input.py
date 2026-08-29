"""真实 git 输入 + 描述来源（实现 4/11 #16）的 CLI 缝测试。

覆盖：precheck/check 自调 git、merge-base 基线（含分叉历史）、config.base 与
显式 ref、描述自动拼接与显式来源标注、非 git 目录 / 路径不存在 / 参数冲突 → 64、
空 diff → pass 短路。git 仓库用 tmp_path + subprocess 真造（Windows 下显式
encoding="utf-8"；commit 加 --no-gpg-sign 防全局 gpgsign 干扰）；
模型一律 ScriptedProvider 注入，全程零网络。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reviewer.model import ScriptedProvider


def _git(repo: Path, *args: str) -> str:
    """测试内跑 git（显式 utf-8，失败直接炸出——测试环境问题不应被吞）。"""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout


def _init_repo(tmp_path: Path) -> Path:
    """tmp_path 下造一个本地 git 仓库，固定主分支名 main（不依赖 init.defaultBranch）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        _git(repo, "init", "-b", "main")
    except subprocess.CalledProcessError:  # 老 git 无 -b：init 后手工指到 main
        _git(repo, "init")
        _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(repo: Path, message: str, file: str, content: str) -> None:
    """写文件 + add + commit（--no-gpg-sign 防全局 gpgsign 拖测试下水）。"""
    path = repo / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-gpg-sign", "-m", message)


# ---------------------------------------------------------------------------
# precheck：git diff HEAD（staged + 未提交）
# ---------------------------------------------------------------------------


def test_precheck_collects_staged_and_unstaged(run_cli, panel, tmp_path) -> None:
    """precheck 自调 git：已 staged 与未 staged 的已跟踪文件改动都进 diff。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "base", "a.txt", "base\n")
    _commit(repo, "base2", "b.txt", "b\n")
    (repo / "a.txt").write_text("staged-marker\n", encoding="utf-8")  # 已跟踪，已暂存
    _git(repo, "add", "a.txt")
    (repo / "b.txt").write_text("unstaged-marker\n", encoding="utf-8")  # 已跟踪，未暂存
    provider = panel()
    code, out, err, provider = run_cli(provider, "precheck", "--repo", str(repo), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["mode"] == "mentor"
    prompt = provider.calls[0][1]  # 四专家共享同一配方 user prompt（calls[0] 为并行完成之一）
    assert "staged-marker" in prompt
    assert "unstaged-marker" in prompt
    assert report["metadata"]["base_ref"] == "HEAD"
    # precheck 自动描述恒空（未提交改动无 commit message 可言）→ 来源 none
    assert report["metadata"]["description_source"] == "none"


def test_precheck_unborn_repo_staged_diff(run_cli, panel, tmp_path) -> None:
    """全新仓库（无任何提交）precheck：staged 文件 vs 空树，仍可审、不报错。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("first-marker\n", encoding="utf-8")
    _git(repo, "add", "-A")
    provider = panel()
    code, out, err, provider = run_cli(provider, "precheck", "--repo", str(repo), "--json")
    assert code == 0
    assert "first-marker" in provider.calls[0][1]


# ---------------------------------------------------------------------------
# check：merge-base 基线 + ref 缺省/显式/配置
# ---------------------------------------------------------------------------


def test_check_without_ref_uses_config_base(run_cli, panel, tmp_path) -> None:
    """check 无 ref：基线 = merge-base HEAD <config.base>（缺省 main），模式默认 gatekeeper。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base-content\n")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feat: A", "f1.txt", "feature-marker\n")
    provider = panel()
    code, out, err, provider = run_cli(provider, "check", "--repo", str(repo), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["mode"] == "gatekeeper"
    prompt = provider.calls[0][1]
    assert "feature-marker" in prompt  # 本分支改动进 diff
    assert "base-content" not in prompt  # 基线侧改动不进 diff
    merge_base = _git(repo, "merge-base", "main", "HEAD").strip()
    assert report["metadata"]["base_ref"] == merge_base
    assert report["metadata"]["head_ref"] == "HEAD"


def test_check_base_ref_from_config_file(
    monkeypatch, run_cli, panel, tmp_path
) -> None:
    """config.base 覆盖缺省 main：.reviewer.yaml base: develop，基线取 develop。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    _git(repo, "checkout", "-b", "develop")
    _commit(repo, "d1", "d.txt", "develop-change\n")
    _git(repo, "checkout", "-b", "feature")  # 从 develop 分出
    _commit(repo, "f1", "f.txt", "feature-change\n")
    _git(repo, "checkout", "main")
    _commit(repo, "m1", "m.txt", "main-change\n")
    _git(repo, "checkout", "feature")
    # 配置从 cwd 发现（#15 语义）：cwd 指到带 .reviewer.yaml 的目录
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / ".reviewer.yaml").write_text("base: develop\n", encoding="utf-8")
    monkeypatch.chdir(cfg_dir)
    provider = panel()
    code, out, err, provider = run_cli(provider, "check", "--repo", str(repo), "--json")
    assert code == 0
    report = json.loads(out)
    merge_base = _git(repo, "merge-base", "develop", "HEAD").strip()
    assert report["metadata"]["base_ref"] == merge_base
    prompt = provider.calls[0][1]
    assert "feature-change" in prompt  # feature 相对 develop 的改动
    assert "develop-change" not in prompt  # develop 侧是基线，不进 diff
    assert "main-change" not in prompt  # 用错 main 会带进 develop/main 的分叉改动


def test_check_explicit_ref(run_cli, panel, tmp_path) -> None:
    """check <ref> 显式给基线引用（压过 config.base）。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    _git(repo, "checkout", "-b", "topic")
    _commit(repo, "t1", "t.txt", "topic-marker\n")
    provider = panel()
    code, out, err, provider = run_cli(provider, "check", "main", "--repo", str(repo), "--json")
    assert code == 0
    report = json.loads(out)
    merge_base = _git(repo, "merge-base", "main", "HEAD").strip()
    assert report["metadata"]["base_ref"] == merge_base
    assert "topic-marker" in provider.calls[0][1]


def test_check_merge_base_diverged_history(run_cli, panel, tmp_path) -> None:
    """merge-base 正确性（分叉历史）：只审本分支提交，基线分支的新提交不进 diff。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feat: 分支功能一", "f1.txt", "feature-one\n")
    _commit(repo, "fix: 分支功能二", "f2.txt", "feature-two\n")
    _git(repo, "checkout", "main")
    _commit(repo, "main 独立提交", "m.txt", "main-only\n")
    _git(repo, "checkout", "feature")
    provider = panel()
    code, out, err, provider = run_cli(provider, "check", "main", "--repo", str(repo), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["metadata"]["base_ref"] == _git(repo, "merge-base", "main", "HEAD").strip()
    prompt = provider.calls[0][1]
    assert "feature-one" in prompt
    assert "feature-two" in prompt
    assert "main-only" not in prompt  # merge-base 而非 ref 尖端：main 的新提交被排除


def test_check_bad_ref_exit_64(run_cli, panel, tmp_path) -> None:
    """check 指向不存在的 ref → 64 + 可读错误。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    provider = ScriptedProvider()
    code, out, err, _ = run_cli(provider, "check", "no-such-branch", "--repo", str(repo))
    assert code == 64
    assert "merge-base" in err


# ---------------------------------------------------------------------------
# 描述来源：自动拼接 / 显式 / 冲突
# ---------------------------------------------------------------------------


def test_check_description_auto_assembles_two_commits(run_cli, panel, tmp_path) -> None:
    """描述缺省自动拼接 merge-base..HEAD 的提交信息（subject + body），来源=commits。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feat: 添加导出\n\n详细 body 第一段", "f1.txt", "one\n")
    _commit(repo, "fix: 修正拼写", "f2.txt", "two\n")
    provider = panel()
    code, out, err, provider = run_cli(provider, "check", "--repo", str(repo), "--json")
    assert code == 0
    prompt = provider.calls[0][1]
    assert "feat: 添加导出" in prompt
    assert "详细 body 第一段" in prompt
    assert "fix: 修正拼写" in prompt
    report = json.loads(out)
    assert report["metadata"]["description_source"] == "commits"


def test_explicit_description_marks_source_explicit(run_cli, panel, tmp_path) -> None:
    """--description 显式给出 → description_source=explicit，且压过提交信息拼接。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feat: A", "f1.txt", "one\n")
    provider = panel()
    code, out, err, provider = run_cli(
        provider, "check", "--repo", str(repo), "--description", "自定义意图描述", "--json"
    )
    assert code == 0
    prompt = provider.calls[0][1]
    assert "自定义意图描述" in prompt
    assert "feat: A" not in prompt  # 显式描述优先，提交信息不再拼接
    report = json.loads(out)
    assert report["metadata"]["description_source"] == "explicit"


def test_description_file_marks_source_explicit(run_cli, panel, tmp_path) -> None:
    """--description-file 读 UTF-8 文件为描述（来源 explicit）。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feat: A", "f1.txt", "one\n")
    desc_file = tmp_path / "desc.md"
    desc_file.write_text("文件里的意图", encoding="utf-8")
    provider = panel()
    code, out, err, provider = run_cli(
        provider, "check", "--repo", str(repo), "--description-file", str(desc_file), "--json"
    )
    assert code == 0
    assert "文件里的意图" in provider.calls[0][1]
    report = json.loads(out)
    assert report["metadata"]["description_source"] == "explicit"


def test_description_and_file_conflict_exit_64(run_cli, tmp_path) -> None:
    """--description 与 --description-file 同给 → 64（二选一），模型零调用。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    desc_file = tmp_path / "desc.md"
    desc_file.write_text("x", encoding="utf-8")
    provider = ScriptedProvider()
    code, out, err, _ = run_cli(
        provider, "check", "--repo", str(repo),
        "--description", "a", "--description-file", str(desc_file),
    )
    assert code == 64
    assert "二选一" in err
    assert provider.calls == []


# ---------------------------------------------------------------------------
# 错误路径：非 git 目录 / 路径不存在
# ---------------------------------------------------------------------------


def test_not_a_git_repo_exit_64(run_cli, tmp_path) -> None:
    """非 git 目录运行 → 可读中文错误 + 64（precheck 与 check 一致）。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    provider = ScriptedProvider()
    code1, _, err1, _ = run_cli(provider, "precheck", "--repo", str(plain))
    assert code1 == 64
    assert "不是 git 仓库" in err1
    code2, _, err2, _ = run_cli(provider, "check", "--repo", str(plain))
    assert code2 == 64
    assert "不是 git 仓库" in err2
    assert provider.calls == []


def test_repo_path_missing_exit_64(run_cli, tmp_path, diff_file) -> None:
    """--repo 指向不存在的路径 → 64（回放与 git 模式一致校验）。"""
    missing = tmp_path / "nope"
    provider = ScriptedProvider()
    code1, _, err1, _ = run_cli(
        provider, "check", "--repo", str(missing), "--diff-file", str(diff_file)
    )
    assert code1 == 64
    assert "不存在" in err1
    code2, _, err2, _ = run_cli(provider, "precheck", "--repo", str(missing))
    assert code2 == 64
    assert "不存在" in err2


# ---------------------------------------------------------------------------
# 空 diff：短路 pass 空报告
# ---------------------------------------------------------------------------


def test_empty_diff_precheck_passes(run_cli, tmp_path) -> None:
    """空 diff（无改动）→ pass 退出 0，人类渲染提示「无改动」，模型零调用。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    provider = ScriptedProvider()  # 空脚本：短路不应触达模型
    code, out, err, _ = run_cli(provider, "precheck", "--repo", str(repo))
    assert code == 0
    assert "无改动" in out
    assert provider.calls == []


def test_empty_diff_check_json_report(run_cli, tmp_path) -> None:
    """check 模式空 diff（HEAD == 基线）→ pass 空报告 + no_changes 元数据。"""
    repo = _init_repo(tmp_path)
    _commit(repo, "c1", "a.txt", "base\n")
    provider = ScriptedProvider()
    code, out, err, _ = run_cli(provider, "check", "--repo", str(repo), "--json")
    assert code == 0
    report = json.loads(out)
    assert report["summary"]["verdict"] == "pass"
    assert report["findings"] == []
    assert report["metadata"]["no_changes"] is True
    assert report["metadata"]["description_source"] == "none"  # 无提交区间 → 来源 none
    assert provider.calls == []


# ---------------------------------------------------------------------------
# 回放模式的描述来源元数据（#16 additive 字段）
# ---------------------------------------------------------------------------


def test_replay_description_source_metadata(run_cli, diff_file, panel) -> None:
    """回放模式无 --description → description_source=none；base/head 仍无 git 语义。"""
    provider = panel()
    code, out, err, _ = run_cli(provider, "precheck", "--diff-file", str(diff_file), "--json")
    assert code == 0
    metadata = json.loads(out)["metadata"]
    assert metadata["description_source"] == "none"
    assert metadata["base_ref"] is None
    assert metadata["head_ref"] is None
