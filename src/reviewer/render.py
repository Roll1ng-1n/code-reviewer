"""渲染层：人类可读渲染（同一 Report JSON 两种呈现，#21）。

render_human 按 report["mode"] 分支到 ``_render_gatekeeper`` / ``_render_mentor``：
两者共享严重度分组与格式化基础（_group_by_severity），但呈现策略不同——

* Gatekeeper（守门人视角，服务主管）：verdict + headline 头部醒目；findings
  按严重度分组（blocker→concern→nit），组内按 file:line；**nit 组默认折叠**，
  只打印 ``nit × N`` 汇总行（``--show-nits`` 可展开逐条）。
* Mentor（导师视角，服务作者）：教学口吻开场；逐条展开 rationale（「为什么」）
  与 suggestion（「建议」）；按 file:line 组织，不折叠 nit。

数据契约无渲染字段：本层只消费 Report JSON，不向 schema 写入折叠/渲染类键；
``--json`` 输出与渲染无关。颜色为可选加分项（需 ``--color`` 且 tty），
默认纯文本清晰排版，绝不污染默认输出与测试断言。
"""

from __future__ import annotations

from .contract import SEVERITY_ORDER, exit_code_for

# 严重度呈现顺序：blocker → concern → nit
_SEVERITY_SEQ: tuple[str, ...] = ("blocker", "concern", "nit")

# 严重度中文标签（仅用于分组标题，不入数据契约）
_SEVERITY_LABEL: dict[str, str] = {
    "blocker": "blocker（不修不能合并）",
    "concern": "concern（应关注）",
    "nit": "nit（风格/可读性）",
}


def _group_by_severity(findings: list[dict]) -> dict[str, list[dict]]:
    """按严重度分组（blocker/concern/nit），组内按 (file, line) 排序。

    返回的 dict 恒含三键（缺失严重度对应空列表），保证分组呈现稳定可遍历。
    """
    groups: dict[str, list[dict]] = {sev: [] for sev in _SEVERITY_SEQ}
    for finding in findings:
        groups[finding["severity"]].append(finding)
    for sev in _SEVERITY_SEQ:
        groups[sev].sort(key=lambda f: (f["file"], f["line"]))
    return groups


def _header(report: dict) -> list[str]:
    """两种模式共享的头部：模式名 + verdict（含退出码）+ 三级 counts。"""
    summary = report["summary"]
    counts = summary["counts"]
    return [
        f"AI Code Reviewer — {report['mode']} mode",
        f"verdict: {summary['verdict']}  (exit {exit_code_for(summary['verdict'])})",
        f"counts: blocker={counts['blocker']} concern={counts['concern']} nit={counts['nit']}",
    ]


def _format_finding_line(finding: dict) -> str:
    """单条发现的首行：id + 严重度 + 类别 + file:line（两种模式共用）。"""
    return (
        f"{finding['id']}  [{finding['severity']}] {finding['category']}  "
        f"{finding['file']}:{finding['line']}"
    )


def _render_gatekeeper(report: dict, *, show_nits: bool) -> str:
    """守门人渲染：判决醒目，nit 默认折叠（``--show-nits`` 展开）。

    呈现目标：主管 30 秒内判断 PR 是否值得深看——blocker/concern 逐条完整，
    nit 折叠为一行汇总（计数 + 展开提示），聚焦架构与业务逻辑。
    """
    summary = report["summary"]
    groups = _group_by_severity(report["findings"])
    lines = _header(report) + [""]

    if summary.get("headline"):
        lines.append(f"headline: {summary['headline']}")
        lines.append("")

    for sev in _SEVERITY_SEQ:
        items = groups[sev]
        if not items:
            continue
        if sev == "nit" and not show_nits:
            # nit 默认折叠：只出汇总行，不逐条列出（展开入口 = --show-nits）
            lines.append(
                f"nit × {len(items)}（已折叠，--show-nits 展开）"
            )
            lines.append("")
            continue
        lines.append(f"== {_SEVERITY_LABEL[sev]} ==")
        for finding in items:
            lines.append(_format_finding_line(finding))
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
    return "\n".join(lines).rstrip() + "\n"


def _render_mentor(report: dict) -> str:
    """导师渲染：教学口吻，逐条展开 rationale 与 suggestion，不折叠 nit。

    呈现目标：作者在主管看到前修掉低级问题，并学会下次不犯——每条含
    「为什么」（rationale）与「建议」（suggestion），用中文引导语。
    """
    groups = _group_by_severity(report["findings"])
    lines = _header(report) + [""]
    lines.append("提交前的预检，目标是让这次提交一次过：")
    lines.append("")

    for sev in _SEVERITY_SEQ:
        items = groups[sev]
        if not items:
            continue
        lines.append(f"== {_SEVERITY_LABEL[sev]} ==")
        for finding in items:
            lines.append(_format_finding_line(finding))
            lines.append(f"  问题：{finding['message']}")
            if finding.get("rationale"):
                lines.append(f"  为什么：{finding['rationale']}")
            if finding.get("suggestion"):
                lines.append(f"  建议：{finding['suggestion']}")
            lines.append("")

    if not report["findings"]:
        if report["metadata"].get("no_changes"):
            lines.append("无改动：没有可审查的 diff。")
        else:
            lines.append("无发现。")
    return "\n".join(lines).rstrip() + "\n"


def render_human(report: dict, show_nits: bool = False) -> str:
    """同一 Report 数据的人类可读渲染入口（#21 双模式分化）。

    ``show_nits`` 仅对 gatekeeper 有意义（展开默认折叠的 nit 组）；
    mentor 不折叠 nit，忽略该参数。纯函数：只读 report，不写 schema。
    """
    if report["mode"] == "mentor":
        return _render_mentor(report)
    return _render_gatekeeper(report, show_nits=show_nits)
