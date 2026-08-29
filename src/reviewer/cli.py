"""CLI 缝（Spec #12 Testing Decisions：一切行为通过 CLI 命令面 + 回放模式可观测）。

退出码契约（#7）：pass→0 / blocked→1 / concerns→2 / 参数或配置错误 64 / 中断 130；
运行时错误（含模型调用失败）→ 70；命令面存在但未实现 → 69。
argparse 默认 error() 退出码 2 会与 concerns 撞码，故自定义 Parser.error → 64。
配置分层（#15）：启动早期加载 cwd 的 .reviewer.yaml（非法 → 64）；mode 四层优先级
（--mode 显式 > 子命令默认 > config.mode > 内置缺省）见 _resolve_mode。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, ConfigError, load_config
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


BUILTIN_DEFAULT_MODE = "gatekeeper"  # 内置缺省层：仅当子命令无默认模式时兜底（现有两命令均有）


def _resolve_mode(
    *, explicit: str | None, subcommand_default: str | None, from_config: str | None
) -> str:
    """mode 四层优先级（#15）：--mode 显式 > 子命令默认 > config.mode > 内置缺省。

    规格已定：子命令默认压过 config.mode——``check``/``precheck`` 的模式语义
    不被仓库配置偷换；config.mode 供未来无默认模式的子命令兜底使用。
    """
    if explicit:
        return explicit
    if subcommand_default:
        return subcommand_default
    if from_config:
        return from_config
    return BUILTIN_DEFAULT_MODE


def _run_review(
    *, diff: str, mode: str, description: str, repo: Path, config: Config
) -> dict:
    provider = make_provider(config)
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


def _cmd_check(args: argparse.Namespace, config: Config) -> int:
    return _run_replay_command(args, config=config, default_mode="gatekeeper")


def _cmd_precheck(args: argparse.Namespace, config: Config) -> int:
    return _run_replay_command(args, config=config, default_mode="mentor")


def _run_replay_command(
    args: argparse.Namespace, *, config: Config, default_mode: str
) -> int:
    mode = _resolve_mode(
        explicit=args.mode, subcommand_default=default_mode, from_config=config.mode
    )
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
    report = _run_review(
        diff=diff, mode=mode, description=args.description, repo=args.repo, config=config
    )
    _emit(report, as_json=args.as_json)
    return exit_code_for(report["summary"]["verdict"])


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = load_config()  # 启动早期加载；配置非法 → 64（在图运行前拦下）
        if args.command == "check":
            return _cmd_check(args, config)
        if args.command == "precheck":
            return _cmd_precheck(args, config)
        if args.command == "pr":
            print("reviewer: pr 命令为预留命令面（PR 集成为后续章节）", file=sys.stderr)
            return EXIT_UNAVAILABLE
        raise ReviewerError(f"未知命令：{args.command}")  # pragma: no cover
    except ConfigError as exc:
        # 配置层错误（YAML/字段/密钥缺失，含 make_provider 构造期）→ 64；
        # 运行期模型调用失败走 ReviewerError → 70，两者语义区分。
        print(f"reviewer: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    except ReviewerError as exc:
        print(f"reviewer: {exc}", file=sys.stderr)
        return EXIT_SOFTWARE
    except KeyboardInterrupt:
        print("reviewer: 已中断", file=sys.stderr)
        return EXIT_INTERRUPTED
