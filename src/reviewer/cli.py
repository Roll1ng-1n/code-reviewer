"""CLI 缝（Spec #12 Testing Decisions：一切行为通过 CLI 命令面 + 回放模式可观测）。

退出码契约（#7）：pass→0 / blocked→1 / concerns→2 / 参数错误 64 / 中断 130；
运行时错误（含模型调用失败）→ 70；命令面存在但未实现 → 69。
argparse 默认 error() 退出码 2 会与 concerns 撞码，故自定义 Parser.error → 64。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .contract import (
    EXIT_INTERRUPTED,
    EXIT_SOFTWARE,
    EXIT_UNAVAILABLE,
    EXIT_USAGE_ERROR,
    SCHEMA_VERSION,
    exit_code_for,
)
from .graph import build_review_graph
from .model import make_provider
from .render import render_human


class ReviewerError(RuntimeError):
    """运行时错误（模型调用失败、图执行失败等）。"""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse 默认 error() 退出码 2 与 verdict=concerns 撞码，改为 64。"""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE_ERROR)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--diff-file", type=Path, help="回放模式：从固定 diff 文件读入（评估地基）")
    parser.add_argument("--repo", type=Path, default=Path("."), help="仓库路径（元数据用）")
    parser.add_argument("--description", default="", help="PR 描述/意图文本（logic 专家的意图上下文）")
    parser.add_argument("--mode", choices=("mentor", "gatekeeper"), help="覆盖子命令默认模式")
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出完整 Report JSON（schema-v1）")


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="reviewer", description="AI code reviewer（LangGraph 单图双模式）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="守门审查：对分支/引用出判决式报告（默认 gatekeeper）")
    _add_common_args(check)

    precheck = sub.add_parser("precheck", help="提交前预检：导师式报告（默认 mentor）")
    _add_common_args(precheck)

    pr = sub.add_parser("pr", help="reviewer pr <n>（预留命令面，PR 集成为后续章节）")
    pr.add_argument("number", type=int)
    return parser


def _run_review(*, diff: str, mode: str, description: str, repo: Path) -> dict:
    provider = make_provider()
    graph = build_review_graph(provider)
    start = time.monotonic()
    try:
        final = graph.invoke({"diff": diff, "mode": mode, "description": description})
    except Exception as exc:
        raise ReviewerError(f"评审运行失败：{exc}") from exc
    duration_ms = round((time.monotonic() - start) * 1000)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "summary": final["summary"],
        "findings": final["findings"],
        "metadata": {
            "repo": repo.resolve().name,
            "base_ref": None,  # 回放模式无 git 语义；真实 git 输入由 #16 接入后填
            "head_ref": None,
            "model": provider.model_name,
            "spec_kb": {"loaded": False, "documents": 0, "hash": None},  # #19 接入
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }


def _emit(report: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report))


def _cmd_check(args: argparse.Namespace) -> int:
    return _run_replay_command(args, default_mode="gatekeeper")


def _cmd_precheck(args: argparse.Namespace) -> int:
    return _run_replay_command(args, default_mode="mentor")


def _run_replay_command(args: argparse.Namespace, *, default_mode: str) -> int:
    mode = args.mode or default_mode
    if args.diff_file is None:
        print(
            f"reviewer: {args.command} 尚未接入真实 git 输入（实现 4/11）；"
            "当前请用 --diff-file 回放模式",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    try:
        diff = args.diff_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"reviewer: 无法读取 diff 文件：{exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    report = _run_review(diff=diff, mode=mode, description=args.description, repo=args.repo)
    _emit(report, as_json=args.as_json)
    return exit_code_for(report["summary"]["verdict"])


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "precheck":
            return _cmd_precheck(args)
        if args.command == "pr":
            print("reviewer: pr 命令为预留命令面（PR 集成为后续章节）", file=sys.stderr)
            return EXIT_UNAVAILABLE
        raise ReviewerError(f"未知命令：{args.command}")  # pragma: no cover
    except ReviewerError as exc:
        print(f"reviewer: {exc}", file=sys.stderr)
        return EXIT_SOFTWARE
    except KeyboardInterrupt:
        print("reviewer: 已中断", file=sys.stderr)
        return EXIT_INTERRUPTED
