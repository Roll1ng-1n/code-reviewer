"""CLI 缝测试公共设施（#14）。

注入点：monkeypatch ``reviewer.cli.make_provider`` → 返回 :class:`ScriptedProvider`，
进程内直调 ``reviewer.cli.main``，全程零网络（真实模型调用被完全替换）。
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from reviewer import cli
from reviewer.model import ScriptedProvider

# 最小 unified diff（内容本身不被解析，仅作为喂给专家的回放文本）
SAMPLE_DIFF = """--- a/src/calc.py
+++ b/src/calc.py
@@ -1,3 +1,4 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def _findings_response(findings: list[dict[str, Any]]) -> str:
    """构造专家 findings JSON 响应文本（模拟模型输出的严格 JSON 契约）。"""
    return json.dumps({"findings": findings}, ensure_ascii=False)


@pytest.fixture
def findings_json() -> Callable[[list[dict[str, Any]]], str]:
    """工厂 fixture：findings 列表 → 专家响应文本。"""
    return _findings_response


@pytest.fixture
def make_finding() -> Callable[..., dict[str, Any]]:
    """工厂 fixture：造一条通过 Finding 校验的合法发现（category 钉死 logic）。"""

    def _make(**overrides: Any) -> dict[str, Any]:
        finding: dict[str, Any] = {
            "file": "a.py",
            "line": 1,
            "severity": "nit",
            "category": "logic",
            "message": "m",
        }
        finding.update(overrides)
        return finding

    return _make


@pytest.fixture
def diff_file(tmp_path: Path) -> Path:
    """回放模式 diff 文件（tmp_path 造，测后自动清理）。"""
    path = tmp_path / "change.diff"
    path.write_text(SAMPLE_DIFF, encoding="utf-8")
    return path


CliRunner = Callable[..., tuple[int, str, str, ScriptedProvider]]


@pytest.fixture
def run_cli(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """进程内直调 main()：注入假 provider 并捕获输出。

    返回 helper ``(provider, *argv) -> (退出码, stdout, stderr, provider)``。
    argparse 用法错误走 SystemExit(64)，不在本 helper 内捕获（测试单独断言）。
    """

    def _run(
        provider: ScriptedProvider, *argv: str
    ) -> tuple[int, str, str, ScriptedProvider]:
        monkeypatch.setattr(cli, "make_provider", lambda: provider)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue(), provider

    return _run
