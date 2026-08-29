"""渲染层（曳光弹版：单一人类可读渲染）。

mentor/gatekeeper 渲染分化（nit 折叠、导师式口吻）全部在本层，
由 实现 10/11（#21）接管；同一 Report 数据只换渲染，不漂移。
"""

from __future__ import annotations

from .contract import exit_code_for


def render_human(report: dict) -> str:
    summary = report["summary"]
    counts = summary["counts"]
    lines = [
        f"AI Code Reviewer — {report['mode']} mode",
        f"verdict: {summary['verdict']}  (exit {exit_code_for(summary['verdict'])})",
        f"counts: blocker={counts['blocker']} concern={counts['concern']} nit={counts['nit']}",
        "",
    ]
    for finding in report["findings"]:
        lines.append(
            f"{finding['id']}  [{finding['severity']}] {finding['category']}  "
            f"{finding['file']}:{finding['line']}"
        )
        lines.append(f"  {finding['message']}")
        if finding.get("rationale"):
            lines.append(f"  why: {finding['rationale']}")
        if finding.get("suggestion"):
            lines.append(f"  fix: {finding['suggestion']}")
        lines.append("")
    if not report["findings"]:
        # 空 diff 短路报告（#16）携带 no_changes 元数据 → 提示「无改动」而非「无发现」
        if report["metadata"].get("no_changes"):
            lines.append("无改动：没有可审查的 diff。")
        else:
            lines.append("无发现。")
    return "\n".join(lines)
