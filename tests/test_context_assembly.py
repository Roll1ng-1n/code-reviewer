"""上下文组装（#17）：结构地图 + import 邻域的 CLI 缝测试。

tmp_path 造快照仓库（.py 相互 import + AGENTS.md），ScriptedProvider 全程零网络：
邻域直接依赖进 prompt、AGENTS.md 并入结构地图、两类截断标志、repo_root 缺失 /
被改文件不在快照 / 语法错误的降级路径、metadata.context_stats 可观测。
"""

from __future__ import annotations

import json
from pathlib import Path

from reviewer.context import assemble_context
from reviewer.model import ScriptedProvider

# 依赖文件内容的标志性文本（断言进 prompt 用的唯一锚）
HELPER_LANDMARK = "HELPER_LANDMARK_6271"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _calc_diff() -> str:
    """最小 unified diff：被改文件 = pkg/calc.py。"""
    return (
        "--- a/pkg/calc.py\n"
        "+++ b/pkg/calc.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )


def _make_snapshot_repo(tmp_path: Path) -> Path:
    """快照仓库：AGENTS.md + pkg 包内 calc.py（被改）→ helper.py（直接依赖）。"""
    repo = tmp_path / "snapshot"
    _write(repo / "AGENTS.md", "# 模块地图\n\n- pkg.calc：四则运算入口\n")
    _write(repo / "pkg" / "__init__.py", "")
    _write(
        repo / "pkg" / "calc.py",
        "from pkg.helper import compute\n\n\ndef add(a, b):\n    return compute(a, b)\n",
    )
    _write(
        repo / "pkg" / "helper.py",
        f"def compute(a, b):\n    return a + b  # {HELPER_LANDMARK}\n",
    )
    return repo


# ---------------------------------------------------------------------------
# CLI 缝（ScriptedProvider 零网络）
# ---------------------------------------------------------------------------


def test_neighborhood_deps_agents_md_description_enter_prompt(
    run_cli, findings_json, tmp_path
) -> None:
    """四块上下文齐进 prompt：结构地图（含 AGENTS.md）+ diff + 描述 + 邻域依赖。"""
    repo = _make_snapshot_repo(tmp_path)
    diff = tmp_path / "change.diff"
    _write(diff, _calc_diff())
    provider = ScriptedProvider([findings_json([])])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff), "--repo", str(repo),
        "--description", "修复加法行为", "--json",
    )
    assert code == 0
    prompt = provider.calls[0][1]
    # 邻域：直接依赖 helper.py 的标志性内容进 user prompt
    assert HELPER_LANDMARK in prompt
    assert "## import 邻域" in prompt
    assert "pkg/helper.py" in prompt
    # 结构地图：目录树 + AGENTS.md 模块职责并入
    assert "## 仓库结构地图" in prompt
    assert "pkg/calc.py" in prompt
    assert "pkg.calc：四则运算入口" in prompt
    # 描述块与 diff 块仍在
    assert "## 意图描述" in prompt
    assert "修复加法行为" in prompt
    assert "```diff" in prompt
    # 预算统计：邻域收录 1 个依赖，无截断，无未解析
    stats = json.loads(out)["metadata"]["context_stats"]
    assert stats["changed_py_files"] == 1
    assert stats["neighborhood_files"] == 1
    assert stats["neighborhood_tokens"] > 0
    assert stats["unresolved_files"] == 0
    assert stats["structure_map_truncated"] is False
    assert stats["neighborhood_truncated"] is False


def test_structure_map_tree_without_agents_md(run_cli, findings_json, tmp_path) -> None:
    """无 AGENTS.md → 结构地图仍有目录树；无 import / 无描述 → 空块整体省略。"""
    repo = tmp_path / "bare"
    _write(repo / "pkg" / "calc.py", "x = 1\n")
    diff = tmp_path / "change.diff"
    _write(diff, _calc_diff())
    provider = ScriptedProvider([findings_json([])])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff), "--repo", str(repo), "--json"
    )
    assert code == 0
    prompt = provider.calls[0][1]
    assert "## 仓库结构地图" in prompt
    assert "pkg/calc.py" in prompt
    # 无依赖 / 无描述 → 对应块不留空标题
    assert "## import 邻域" not in prompt
    assert "## 意图描述" not in prompt
    stats = json.loads(out)["metadata"]["context_stats"]
    assert stats["neighborhood_files"] == 0
    assert stats["neighborhood_tokens"] == 0


def test_structure_map_truncation_flag(run_cli, findings_json, tmp_path) -> None:
    """>500 行目录树 → 截断尾注进 prompt + structure_map_truncated 标志。"""
    repo = tmp_path / "big"
    repo.mkdir()
    for i in range(600):  # 600 个空目录 → 600 行目录树
        (repo / f"d{i:03d}").mkdir()
    diff = tmp_path / "change.diff"
    _write(diff, "--- a/x.txt\n+++ b/x.txt\n@@\n-a\n+a\n")  # 非 .py：邻域不触发
    provider = ScriptedProvider([findings_json([])])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff), "--repo", str(repo), "--json"
    )
    assert code == 0
    assert "[truncated " in provider.calls[0][1]
    stats = json.loads(out)["metadata"]["context_stats"]
    assert stats["structure_map_truncated"] is True
    assert stats["structure_map_lines"] == 500  # ≤500 含尾注
    assert stats["changed_py_files"] == 0
    assert stats["neighborhood_files"] == 0


def test_neighborhood_budget_truncation(run_cli, findings_json, tmp_path) -> None:
    """依赖文件超 2000 token 预算 → 截断塞满 + [budget truncated] 尾注 + 标志。"""
    repo = tmp_path / "snap"
    _write(repo / "pkg" / "calc.py", "from pkg.helper import compute\n")
    _write(repo / "pkg" / "helper.py", "x = 'pad'  # " + "A" * 10000 + "\n")
    diff = tmp_path / "change.diff"
    _write(diff, _calc_diff())
    provider = ScriptedProvider([findings_json([])])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff), "--repo", str(repo), "--json"
    )
    assert code == 0
    prompt = provider.calls[0][1]
    assert "[budget truncated]" in prompt
    stats = json.loads(out)["metadata"]["context_stats"]
    assert stats["neighborhood_truncated"] is True
    assert stats["neighborhood_files"] == 1  # 截断塞满预算的那个文件计入
    assert 1900 <= stats["neighborhood_tokens"] < 2100  # 启发式估算贴近预算线


def test_neighborhood_empty_when_files_not_in_snapshot(
    run_cli, findings_json, tmp_path
) -> None:
    """被改文件不在快照仓库（纯回放常见）→ 邻域为空、计数 0、不致命。"""
    empty_repo = tmp_path / "no-py"
    empty_repo.mkdir()
    diff = tmp_path / "change.diff"
    _write(diff, _calc_diff())
    provider = ScriptedProvider([findings_json([])])
    code, out, err, _ = run_cli(
        provider, "check", "--diff-file", str(diff), "--repo", str(empty_repo), "--json"
    )
    assert code == 0
    assert "## import 邻域" not in provider.calls[0][1]
    stats = json.loads(out)["metadata"]["context_stats"]
    assert stats["changed_py_files"] == 1
    assert stats["unresolved_files"] == 1
    assert stats["neighborhood_files"] == 0
    assert stats["neighborhood_tokens"] == 0


# ---------------------------------------------------------------------------
# context 纯函数的降级路径单元测试（repo_root 缺失等 CLI 触不到的形态）
# ---------------------------------------------------------------------------


def test_assemble_context_without_repo_root_degrades_to_empty() -> None:
    """repo_root=None（纯回放无快照）→ 两块上下文皆空、计数 0，不致命。"""
    result = assemble_context(diff=_calc_diff(), repo_root=None, description="d")
    assert result["structure_map"] == ""
    assert result["neighborhood"] == ""
    stats = result["context_stats"]
    assert stats["structure_map_lines"] == 0
    assert stats["changed_py_files"] == 1
    assert stats["neighborhood_files"] == 0


def test_relative_import_resolves_sibling_dependency(tmp_path) -> None:
    """相对导入（from .helper import x）解析同包直接依赖。"""
    repo = tmp_path / "rel"
    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "calc.py", "from .helper import compute\n")
    _write(repo / "pkg" / "helper.py", "MARKER_REL_991 = True\n")
    result = assemble_context(diff=_calc_diff(), repo_root=str(repo), description="")
    assert "MARKER_REL_991" in result["neighborhood"]
    assert result["context_stats"]["neighborhood_files"] == 1


def test_syntax_error_changed_file_skipped_not_fatal(tmp_path) -> None:
    """快照内被改文件语法错误 → ast 解析失败跳过（不致命），邻域为空可观测。"""
    repo = tmp_path / "broken"
    _write(repo / "pkg" / "calc.py", "def broken(:\n")
    result = assemble_context(diff=_calc_diff(), repo_root=str(repo), description="")
    assert result["neighborhood"] == ""
    stats = result["context_stats"]
    assert stats["unresolved_files"] == 1
    assert stats["neighborhood_files"] == 0
