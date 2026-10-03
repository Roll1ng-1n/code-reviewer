"""Confined Replay reads and immutable check source views (#28, #35)."""
import json
import os
import subprocess

import pytest

from test_git_input import _commit, _git, _init_repo


@pytest.mark.parametrize("escape", ["relative_import", "diff_path", "prefix", "symlink", "drive"])
def test_replay_never_reads_outside_repo(escape, run_cli, panel, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "private.py"
    outside.write_text("OUTSIDE_SENTINEL = True\n", encoding="utf-8")
    main = repo / "main.py"
    changed = "main.py"
    if escape == "relative_import":
        main.write_text("from .. import private\n", encoding="utf-8")
    elif escape == "diff_path":
        outside.write_text("import private\n", encoding="utf-8")
        changed = "../private.py"
    elif escape == "prefix":
        sibling = tmp_path / "repo-other"
        sibling.mkdir()
        (sibling / "main.py").write_text("import private\n", encoding="utf-8")
        changed = "../repo-other/main.py"
    elif escape == "drive":
        changed = "C:/outside.py" if os.name == "nt" else str(outside)
    else:
        main.write_text("import private\n", encoding="utf-8")
        try:
            (repo / "private.py").symlink_to(outside)
        except OSError as exc:
            if os.name != "nt":
                raise
            # Directory junctions provide the same resolve-boundary regression
            # without requiring the Windows symlink privilege.
            external = tmp_path / "external"
            external.mkdir()
            (external / "__init__.py").write_text("OUTSIDE_SENTINEL = True\n", encoding="utf-8")
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(repo / "private"), str(external)], capture_output=True)
            assert proc.returncode == 0, f"junction unavailable: {exc}"
    diff = tmp_path / "change.diff"
    diff.write_text(f"--- a/{changed}\n+++ b/{changed}\n@@ -0,0 +1 @@\n+pass\n", encoding="utf-8")
    code, out, _, provider = run_cli(panel(), "check", "--diff-file", str(diff), "--repo", str(repo), "--json")
    assert code == 0
    assert all("OUTSIDE_SENTINEL" not in u for _, u in provider.calls)
    assert json.loads(out)["metadata"]["context_stats"]["outside_paths_skipped"] >= 1


def test_check_context_is_pinned_to_reviewed_commit(run_cli, panel, tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "base", "dep.py", "COMMITTED_DEP = True\n")
    _commit(repo, "roles", "AGENTS.md", "COMMITTED_ROLES\n")
    _commit(repo, "main", "main.py", "import dep\n")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feature", "main.py", "import dep\nFEATURE = True\n")
    first = panel()
    code, out, _, first = run_cli(first, "check", "main", "--repo", str(repo), "--json")
    assert code == 0
    original_prompts = sorted(first.calls)
    (repo / "dep.py").write_text("UNCOMMITTED_DEP = True\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("UNCOMMITTED_ROLES\n", encoding="utf-8")
    (repo / "main.py").unlink()
    (repo / "untracked").mkdir()
    (repo / "untracked/x.py").write_text("UNTRACKED\n", encoding="utf-8")
    before = _git(repo, "status", "--porcelain")
    code, out, _, second = run_cli(panel(), "check", "main", "--repo", str(repo), "--json")
    assert code == 0
    assert sorted(second.calls) == original_prompts
    assert _git(repo, "status", "--porcelain") == before
    assert all("COMMITTED_DEP" in u and "COMMITTED_ROLES" in u for _, u in second.calls)
    assert json.loads(out)["metadata"]["source_commit"] == _git(repo, "rev-parse", "HEAD").strip()


def test_precheck_and_replay_use_working_snapshot(run_cli, panel, tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "base", "dep.py", "ORIGINAL_DEP = True\n")
    _commit(repo, "main", "main.py", "import dep\n")
    (repo / "dep.py").write_text("WORKING_DEP = True\n", encoding="utf-8")
    (repo / "main.py").write_text("import dep\nUNCOMMITTED = True\n", encoding="utf-8")
    # The imported dependency is also changed: use a separate unchanged dependency
    # to check neighborhood rather than the diff block.
    (repo / "other.py").write_text("WORKING_NEIGHBOR = True\n", encoding="utf-8")
    (repo / "main.py").write_text("import other\nUNCOMMITTED = True\n", encoding="utf-8")
    code, _, _, provider = run_cli(panel(), "precheck", "--repo", str(repo), "--json")
    assert code == 0
    assert all("WORKING_NEIGHBOR" in u for _, u in provider.calls)
    diff = tmp_path / "replay.diff"
    diff.write_text("+++ b/main.py\n", encoding="utf-8")
    code, _, _, provider = run_cli(panel(), "check", "--diff-file", str(diff), "--repo", str(repo), "--json")
    assert code == 0
    assert all("WORKING_NEIGHBOR" in u for _, u in provider.calls)
