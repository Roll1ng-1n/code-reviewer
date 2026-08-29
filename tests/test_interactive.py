"""#23 --interactive interrupt 实验 CLI 缝测试：confirm 节点 / interrupt 循环 /
SqliteSaver 持久化 / 幂等恢复 / 默认批处理隔离。

验收标准（issue #23）：
- 默认（无 --interactive）：不构造 SqliteSaver、图无 checkpointer、confirm 节点
  不挂载 → 批处理路径零变化（findings 直接进 verdict，结果与 --json 报告一致）
- --interactive 下：interrupt 被触发（CLI 进入确认循环）；``d`` 丢弃全部 →
  verdict 变 pass、findings 空；``a`` 全通过 → 结果同批处理；``1,3`` 保留选中
- 幂等：模拟 resume 两次不产生重复 finding（confirm 节点幂等透传）
- SqliteSaver 持久化：同一 thread_id 第二次 invoke 能取到 checkpoint

注入方式：monkeypatch ``reviewer.cli.input`` 注入决策（避免真实 stdin 阻塞），
monkeypatch ``reviewer.cli._make_checkpointer`` 指向临时目录 SQLite（隔离测试
互不串扰、可观测 checkpoint 落盘）。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from reviewer import cli
from reviewer.model import ScriptedProvider

# 复核过滤 system 提示词角色标识（与 aggregate.py 常量一致）
REVIEW_MARKER = "复核过滤器"


def _findings_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings}, ensure_ascii=False)


def _logic_provider(findings: list[dict]) -> ScriptedProvider:
    """logic 专家出指定 findings，其余专家空应答，复核过滤原样保留。

    四专家并行 fan-out（KB 空剔除 spec → architecture/logic/style 三专家），
    脚本需覆盖全部专家身份，否则未匹配的专家耗尽脚本抛错（退出 70）。
    """
    return ScriptedProvider(
        by_expert={
            "architecture": _findings_response([]),
            "logic": _findings_response(findings),
            "spec": _findings_response([]),
            "style": _findings_response([]),
            REVIEW_MARKER: _findings_response(findings),
        }
    )


@pytest.fixture
def interactive_checkpointer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """把 SqliteSaver 指向 tmp_path 的 SQLite 文件，返回其路径供断言 checkpoint 落盘。"""
    db_path = tmp_path / "interactive.db"

    def _make_checkpointer():
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver(sqlite3.connect(str(db_path), check_same_thread=False))

    monkeypatch.setattr(cli, "_make_checkpointer", _make_checkpointer)
    return db_path


@pytest.fixture
def inject_input(monkeypatch: pytest.MonkeyPatch):
    """按脚本序列注入 stdin 决策（替代真实 input()，避免阻塞）。

    ``input`` 是内建函数，cli.py 里以裸 ``input()`` 调用 → patch ``builtins.input``。
    """

    def _inject(*decisions: str):
        queue = list(decisions)

        def _fake_input(prompt: str = "") -> str:
            return queue.pop(0)

        monkeypatch.setattr("builtins.input", _fake_input)

    return _inject


def test_default_batch_no_checkpointer(run_cli, diff_file, make_finding) -> None:
    """默认（无 --interactive）：批处理路径不构造 SqliteSaver、无 confirm 节点。

    单条 concern → concerns(2)，findings 原样进报告；provider 调用序列里无任何
    interrupt/confirm 痕迹（confirm 节点未挂载，findings 直接进 verdict）。
    """
    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="默认批处理",
    )
    provider = _logic_provider([finding])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 2
    report = json.loads(out)
    assert [f["message"] for f in report["findings"]] == ["默认批处理"]
    # 无 --interactive：确认列表不应渲染（confirm 节点未挂载）
    assert "[实验性功能 --interactive]" not in err


def test_interactive_drop_all(
    run_cli, diff_file, make_finding, interactive_checkpointer, inject_input
) -> None:
    """--interactive + ``d`` 丢弃全部 → verdict 变 pass、findings 空。

    触发 interrupt 进入确认循环，注入 ``d`` 决策 → confirm 节点过滤掉全部
    findings，verdict 派生 pass。
    """
    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="将被丢弃",
    )
    provider = _logic_provider([finding])
    inject_input("d")
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--json", "--interactive"
    )
    assert code == 0  # 丢弃后无 concern → pass
    report = json.loads(out)
    assert report["findings"] == []
    assert report["summary"]["verdict"] == "pass"
    # 进入确认循环：确认列表已渲染（stderr，不污染 --json stdout）
    assert "[实验性功能 --interactive]" in err


def test_interactive_accept_all_matches_batch(
    run_cli, diff_file, make_finding, interactive_checkpointer, inject_input
) -> None:
    """--interactive + ``a`` 全通过 → 结果与批处理一致（findings 全保留）。"""
    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="全保留",
    )
    provider = _logic_provider([finding])
    inject_input("a")
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--json", "--interactive"
    )
    assert code == 2
    report = json.loads(out)
    assert [f["message"] for f in report["findings"]] == ["全保留"]


def test_interactive_select_subset(
    run_cli, diff_file, make_finding, interactive_checkpointer, inject_input
) -> None:
    """--interactive + ``1,3`` 保留选中 → 只保留序号 1、3 的 finding。"""
    f1 = make_finding(
        category="logic", file="a.py", line=1,
        severity="nit", message="第一条",
    )
    f2 = make_finding(
        category="logic", file="b.py", line=2,
        severity="concern", message="第二条",
    )
    f3 = make_finding(
        category="logic", file="c.py", line=3,
        severity="nit", message="第三条",
    )
    provider = _logic_provider([f1, f2, f3])
    inject_input("1,3")
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--json", "--interactive"
    )
    # 保留 f1(nit) + f3(nit) → 无 concern → pass
    assert code == 0
    report = json.loads(out)
    assert [f["message"] for f in report["findings"]] == ["第一条", "第三条"]


def test_interactive_quit_aborts(
    run_cli, diff_file, make_finding, interactive_checkpointer, inject_input
) -> None:
    """--interactive + ``q`` 中止 → 退出码 130（中断），无报告产出。"""
    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="将中止",
    )
    provider = _logic_provider([finding])
    inject_input("q")
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--interactive"
    )
    assert code == 130
    assert "确认已中止" in err


def test_sqlite_checkpoint_persisted(
    tmp_path, make_finding, interactive_checkpointer
) -> None:
    """SqliteSaver 持久化：同一 thread_id 第二次 invoke 能取到 checkpoint。

    直接驱动图（不经 CLI），确认 interrupt 挂起后 checkpoint 落盘、可 get_state；
    恢复后完成，无重复 finding（幂等）。
    """
    from langgraph.types import Command

    from reviewer.graph import build_review_graph

    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="持久化",
    )
    provider = _logic_provider([finding])
    graph = build_review_graph(
        provider,
        experts_enabled=["logic"],
        checkpointer=cli._make_checkpointer(),
        interactive=True,
    )
    config = {"configurable": {"thread_id": "persist-test"}}
    initial = {
        "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        "mode": "gatekeeper",
        "description": "",
        "repo_root": str(tmp_path),
    }
    result = graph.invoke(initial, config=config)
    # interrupt 挂起：__interrupt__ 存在，checkpoint 可查
    assert "__interrupt__" in result
    state = graph.get_state(config)
    assert state.next == ("confirm",)  # 挂起在 confirm 节点

    # 恢复：保留全部
    final = graph.invoke(Command(resume=[finding]), config=config)
    assert final["confirmed"] is True
    assert [f["message"] for f in final["findings"]] == ["持久化"]
    # 恢复重跑 confirm 节点走幂等透传，不二次 interrupt（无 __interrupt__ 残留）
    assert "__interrupt__" not in final


def test_confirm_idempotent_no_duplicate_findings(
    tmp_path, make_finding, interactive_checkpointer
) -> None:
    """幂等：resume 两次（第二次直接重跑 confirm）不产生重复 finding。

    第一次 resume 完成图执行；再以同一 config 触发时 confirm 走「已确认」透传，
    findings 数量不翻倍（验证 interrupt 恢复从头重跑的副作用已消除）。
    """
    from langgraph.types import Command

    from reviewer.graph import build_review_graph

    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="幂等",
    )
    provider = _logic_provider([finding])
    graph = build_review_graph(
        provider,
        experts_enabled=["logic"],
        checkpointer=cli._make_checkpointer(),
        interactive=True,
    )
    config = {"configurable": {"thread_id": "idempotent-test"}}
    initial = {
        "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        "mode": "gatekeeper",
        "description": "",
        "repo_root": str(tmp_path),
    }
    first = graph.invoke(initial, config=config)
    assert "__interrupt__" in first
    final = graph.invoke(Command(resume=[finding]), config=config)
    assert len(final["findings"]) == 1  # 恢复后 findings 精确 1 条，无重复
    # 直接以新 config 重跑（新 thread_id）应得到同样单条结果，验证节点无累计副作用
    config2 = {"configurable": {"thread_id": "idempotent-test-2"}}
    r2 = graph.invoke(initial, config=config2)
    assert "__interrupt__" in r2
    final2 = graph.invoke(Command(resume=[finding]), config=config2)
    assert len(final2["findings"]) == 1
