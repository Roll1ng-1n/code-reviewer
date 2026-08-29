"""对外契约：Finding 模型、确定性排序/编号、verdict 程序化派生、退出码。

schema-v1 Report 契约（Spec #12；findings 契约定稿于 Wayfinder 票 #6）::

    {
      "schema_version": "1",
      "mode": "mentor | gatekeeper",
      "summary": {"verdict": "pass|concerns|blocked", "headline": "...",
                   "counts": {"blocker": 0, "concern": 0, "nit": 0}},
      "findings": [{"id": "F001", "file": "...", "line": 42, "severity": "...",
                     "category": "...", "message": "≤120字", "rationale": "...",
                     "suggestion": "可选"}],
      "metadata": {...}
    }

行锚定 = 新侧文件行号；无 confidence 字段；``--json`` 输出完整契约。

metadata 是开放映射（additive 演进，不升 schema_version）：#16 起恒含
``description_source``（"explicit" | "commits" | "none"，描述来源标注），
空 diff 短路报告额外含 ``no_changes: true``；#17 起正常路径（非空 diff）
额外含 ``context_stats``（上下文组装预算统计：结构地图行数/截断标志、
邻域文件数/token 近似/截断标志等）；#18 起恒含 ``spec_kb`` 键，#19 起填充
真实加载状态——三键 {loaded: bool, documents: int, hash: str|None}，
hash = sha256(按路径排序内容拼接) 前 12 位（空库 None，loaded = documents>0）；
消费方须容忍未知键。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1"

Severity = Literal["blocker", "concern", "nit"]
Category = Literal["architecture", "logic", "spec", "style"]
Verdict = Literal["pass", "concerns", "blocked"]

# 排序权重：blocker 最先
SEVERITY_ORDER: dict[str, int] = {"blocker": 0, "concern": 1, "nit": 2}

# 退出码契约（CLI 契约票 #7）：verdict → exit code
EXIT_CODES: dict[Verdict, int] = {"pass": 0, "blocked": 1, "concerns": 2}

# 非 verdict 退出码
EXIT_USAGE_ERROR = 64  # 参数错误
EXIT_UNAVAILABLE = 69  # 命令面存在但尚未实现（#16 后仅剩 pr 预留；precheck/check 已接真实 git 输入）
EXIT_SOFTWARE = 70  # 运行时错误（模型调用失败、图执行失败）
EXIT_INTERRUPTED = 130  # 中断


def exit_code_for(verdict: str) -> int:
    return EXIT_CODES[verdict]  # type: ignore[index]


class Finding(BaseModel):
    """单条行级锚定发现。专家节点内先经此校验再入 state，不合法条目丢弃并告警。"""

    file: str
    line: int = Field(ge=0)
    severity: Severity
    category: Category
    message: str = Field(min_length=1, max_length=120)
    rationale: str = ""
    suggestion: str | None = None

    @field_validator("file")
    @classmethod
    def _posix_path(cls, value: str) -> str:
        return value.replace("\\", "/")


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """确定性排序（文件, 严重度, 行号）。

    聚合节点必须重排：并行 superstep 的更新合并顺序不保证
    （research/langgraph-orchestration §1.4）。
    """
    return sorted(
        findings,
        key=lambda f: (f["file"], SEVERITY_ORDER[f["severity"]], f["line"]),
    )


def number_findings(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """统一编号 F001..（必须在排序之后调用）。"""
    return [{**finding, "id": f"F{i:03d}"} for i, finding in enumerate(ordered, start=1)]


def derive_verdict(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """verdict 程序化派生：blocker≥1→blocked、concern≥1→concerns、否则 pass。

    零 LLM 调用；聚合护栏（合并只降不升等）由 实现 8/11（#20）在聚合层执行。
    """
    counts: dict[str, int] = {"blocker": 0, "concern": 0, "nit": 0}
    for finding in findings:
        counts[finding["severity"]] += 1
    if counts["blocker"] >= 1:
        verdict: Verdict = "blocked"
    elif counts["concern"] >= 1:
        verdict = "concerns"
    else:
        verdict = "pass"
    if verdict == "pass":
        headline = "未发现 blocker 或 concern"
    else:
        headline = (
            f"{verdict}：{counts['blocker']} blocker / "
            f"{counts['concern']} concern / {counts['nit']} nit"
        )
    return {"verdict": verdict, "headline": headline, "counts": counts}
