"""CLI 缝（Spec #12 Testing Decisions：一切行为通过 CLI 命令面 + 回放模式可观测）。

退出码契约（#7）：pass→0 / blocked→1 / concerns→2 / 参数或配置错误 64 / 中断 130；
运行时错误（含模型调用失败）→ 70；命令面存在但未实现 → 69。
argparse 默认 error() 退出码 2 会与 concerns 撞码，故自定义 Parser.error → 64。
配置分层（#15）：启动早期加载 cwd 的 .reviewer.yaml（非法 → 64）；mode 四层优先级
（--mode 显式 > 子命令默认 > config.mode > 内置缺省）见 _resolve_mode。
真实 git 输入（#16）：precheck/check 缺省自调 git 产出 diff（--diff-file 回放可覆盖）；
check 基线 = merge-base HEAD <ref 缺省 config.base>；描述来源 = 显式参数
（--description / --description-file，二选一）> 提交信息自动拼接（metadata
.description_source 标注）> 无；非 git 目录 → 64。
上下文组装（#17）：context-assembly 图节点产出结构地图 / import 邻域 / 预算统计，
经 metadata.context_stats 可观测（additive，不升 schema_version）。
专家团拓扑（#18）：experts.enabled 送达 router（纯规则过滤），Send fan-out
并行四专家，findings 经 operator.add reducer 汇合后聚合。
Spec KB（#19）：--spec（可重复 / 逗号分隔）+ spec_kb.paths + 约定目录
（repo/specs/、repo/.reviewer/specs/）三层合并加载（相对路径解析基准：回放 =
--repo，git = cwd），metadata.spec_kb 三键 {loaded, documents, hash} 可观测
（additive，contract.py 注释）；KB 空 → router 剔除 spec 专家（可留空落图）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from contextlib import nullcontext

from langgraph.types import Command

from .config import Config, ConfigError, load_config
from .contract import (
    EXIT_INTERRUPTED,
    EXIT_SOFTWARE,
    EXIT_UNAVAILABLE,
    EXIT_USAGE_ERROR,
    SCHEMA_VERSION,
    exit_code_for,
)
from .gitinput import GitInputError, collect_check_diff, collect_precheck_diff, committed_snapshot
from .graph import build_review_graph
from .model import make_provider
from .render import render_human
from .spec_kb import SpecSources


class ReviewerError(RuntimeError):
    """运行时错误（模型调用失败、图执行失败等）。"""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse 默认 error() 退出码 2 与 verdict=concerns 撞码，改为 64。"""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE_ERROR)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--diff-file", type=Path,
        help="回放模式：从 UTF-8 diff 文件读入；用 - 从 stdin 读取；缺省走真实 git 输入",
    )
    parser.add_argument(
        "--repo", type=Path, default=Path("."),
        help="被审仓库路径（须存在）：git 模式 = 在此仓库取 diff；"
             "回放模式 = 被审代码的快照仓库（供上下文组装，#17 消费）",
    )
    parser.add_argument(
        "--description",
        help="PR 描述/意图文本（与 --description-file 二选一，同给 → 64；"
             "缺省自动拼接提交信息，来源见 metadata.description_source）",
    )
    parser.add_argument(
        "--description-file", type=Path,
        help="从 UTF-8 文件读 PR 描述/意图文本（与 --description 二选一）",
    )
    parser.add_argument("--mode", choices=("mentor", "gatekeeper"), help="覆盖子命令默认模式")
    parser.add_argument(
        "--arm", choices=("panel", "baseline"), default="panel",
        help="审查臂（#22 对照实验）：panel = 专家团四专家并行（默认）；"
             "baseline = 单 Agent 融合专家一次调用。两臂共享模型/上下文/schema，"
             "唯一差异是分解",
    )
    parser.add_argument(
        "--spec", action="append", metavar="PATH",
        help="Spec KB 来源：规范文档或目录（目录递归收 *.md；可重复 / 逗号分隔；"
             "优先级最高，压过 spec_kb.paths 与约定目录 specs/、.reviewer/specs/）",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出完整 Report JSON（schema-v1）")
    parser.add_argument(
        "--show-nits", dest="show_nits", action="store_true",
        help="展开 gatekeeper 默认折叠的 nit 组（仅人类可读渲染生效；mentor 不折叠，忽略此参数）",
    )
    parser.add_argument(
        "--interactive", dest="interactive", action="store_true",
        help="实验性功能（#23）：逐条 finding 人工确认（interrupt() + SqliteSaver 持久化）；"
             "默认关闭，仅影响开启时的执行路径",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="reviewer", description="AI code reviewer（LangGraph 单图双模式）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="守门审查：对分支/引用出判决式报告（默认 gatekeeper）")
    check.add_argument(
        "ref", nargs="?", default=None,
        help="基线引用（分支/tag/commit）；缺省用配置 base（缺省 main）——"
             "diff 基线取 merge-base HEAD <ref>。--diff-file 回放模式下忽略",
    )
    _add_common_args(check)

    precheck = sub.add_parser("precheck", help="提交前预检：导师式报告（默认 mentor）")
    _add_common_args(precheck)
    precheck.add_argument(
        "--allow-network", action="store_true",
        help="显式允许预检使用 model 配置（可能将 diff/上下文/规范发送给远程模型）；"
             "缺省使用 precheck.model 本地 GGUF 推理",
    )

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


def _metadata(
    *,
    repo: Path,
    base_ref: str | None,
    head_ref: str | None,
    model: str,
    duration_ms: int,
    description_source: str,
    no_changes: bool,
    context_stats: dict | None = None,
    spec_kb: dict | None = None,
) -> dict:
    """Report metadata（#16 起 additive 开放键，schema_version 不升，见 contract.py 注释）。

    恒含 ``description_source``（"explicit" | "commits" | "none"）；空 diff 短路
    报告额外携带 ``no_changes: true``（机器可区分「审过无发现」与「无可审」）；
    正常路径额外携带 ``context_stats``（#17 上下文预算统计，预算超限与降级可观测）
    与 ``spec_kb``（#19 三键 {loaded, documents, hash}，Spec KB 加载状态）。
    """
    meta: dict = {
        "repo": repo.resolve().name,
        "base_ref": base_ref,  # git 模式 = diff 实际基线（check 为 merge-base SHA）；回放无 git 语义 → None
        "head_ref": head_ref,  # 目前恒 "HEAD"（precheck 的改动在工作区）；回放 → None
        "model": model,
        "spec_kb": spec_kb or {"loaded": False, "documents": 0, "hash": None},  # #19：空 diff 短路即空态
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description_source": description_source,  # 描述来源标注（#16）
    }
    if no_changes:
        meta["no_changes"] = True
    if context_stats is not None:
        meta["context_stats"] = context_stats  # #17：additive，不升 schema_version
    return meta


class _ConfirmAbort(Exception):
    """--interactive 用户选 ``q`` 中止确认（#23）：不产出报告，走中断退出码。"""


def _interactive_thread_id(repo: Path, diff: str) -> str:
    """每次新审查使用独立执行身份；单次 interrupt/resume 复用同一身份。"""
    identity = str(repo.resolve()) + "\0" + diff
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{uuid4().hex}"


def _make_checkpointer():
    """#23：构造 SqliteSaver（本地 SQLite 文件，放系统临时目录）。

    惰性 import：``langgraph.checkpoint.sqlite`` 来自独立包
    ``langgraph-checkpoint-sqlite``（本票新增依赖）。文件落在临时目录，线程短、
    体积小，不污染仓库；生产化再换 PostgresSaver（调研 #2 §4.1）。
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = Path(tempfile.gettempdir()) / "reviewer_interactive_checkpoints.db"
    # from_conn_string 返回 context manager；connect 直接给 sqlite3.Connection
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


def _render_confirm(findings: list[dict]) -> None:
    """#23：渲染确认列表到 stderr（人类可读，不污染 --json 的 stdout 契约）。

    展示每条 finding 的序号 / 严重度 / 文件:行号 / 一句话结论，并提示决策语法：
    ``a`` 全通过 / ``d`` 全丢弃 / ``1,3`` 保留选中 / ``q`` 中止。
    """
    print("\n[实验性功能 --interactive] 请确认以下 findings 是否保留：", file=sys.stderr)
    for f in findings:
        sev = f.get("severity", "?")
        fid = f.get("id", "?")
        loc = f"{f.get('file', '?')}:{f.get('line', '?')}"
        print(f"  [{fid}] ({sev}) {loc}  {f.get('message', '')}", file=sys.stderr)
    print("决策：a=全部保留  d=全部丢弃  1,3=保留选中序号  q=中止", file=sys.stderr)


def _invoke_interactive(graph, initial_state: dict, repo: Path) -> dict:
    """#23：官方 CLI interrupt 循环（调研 #2 §4.2/§4.3）。

    ``graph.invoke`` → 捕获 ``__interrupt__`` 载荷 → 渲染确认列表 → 读用户决策 →
    ``graph.invoke(Command(resume=保留列表), config)`` 循环，直至无 interrupt。
    新调用分配独立 thread_id；本循环的 resume 始终复用该 id。
    """
    config = {"configurable": {"thread_id": _interactive_thread_id(repo, initial_state["diff"])}}
    result = graph.invoke(initial_state, config=config)
    while "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        # 取第一个 interrupt 的 value（本票单 confirm 节点、单次 interrupt）
        payload = interrupts[0].value
        findings = payload.get("findings", [])
        _render_confirm(findings)
        sys.stderr.write("> ")
        sys.stderr.flush()
        try:
            decision = input().strip()
        except EOFError:
            # stdin 无输入（管道/非交互终端）→ 视为中止，不阻塞也不静默放行
            raise _ConfirmAbort()
        kept = _resolve_decision(decision, findings)
        result = graph.invoke(Command(resume=kept), config=config)
    return result


def _resolve_decision(decision: str, findings: list[dict]) -> list[dict]:
    """#23：把用户输入解析为「保留的 findings 列表」。

    - ``a`` → 全部保留；``d`` → 全部丢弃；``q`` → 抛 :class:`_ConfirmAbort` 中止；
    - ``1,3`` → 按序号（findings 已带 F001.. 连续编号，取数字部分匹配）保留选中；
    - 空 / 非法 → 保守全部保留（避免静默丢发现）。
    """
    if decision == "a":
        return findings
    if decision == "d":
        return []
    if decision == "q":
        raise _ConfirmAbort()
    if decision:
        if re.fullmatch(r"[0-9]+(?:(?:\s*,\s*|\s+)[0-9]+)*", decision):
            wanted = {int(token) for token in re.split(r"[\s,]+", decision)}
            if all(1 <= number <= len(findings) for number in wanted):
                return [f for i, f in enumerate(findings, start=1) if i in wanted]
        print("reviewer: 无效确认输入，保守保留全部 Findings", file=sys.stderr)
    # 空 / 无法识别 → 保守全部保留
    return findings


def _run_review(
    *,
    diff: str,
    mode: str,
    description: str,
    description_source: str,
    repo: Path,
    config: Config,
    base_ref: str | None,
    head_ref: str | None,
    spec_sources: SpecSources,
    arm: str,
    interactive: bool = False,
    source_repo: Path | None = None,
    source_commit: str | None = None,
) -> dict:
    provider = make_provider(config)
    # #18 接线：experts.enabled 送达 router（纯规则过滤出运行时分支集合）
    # #19 接线：spec 三层来源打包送达 spec-kb 节点（KB 空 → router 剔除 spec）
    # #22 接线：--arm 送达 router（panel 四专家 fan-out / baseline 单融合专家）
    # #23 接线：interactive 才构造 SqliteSaver + 挂 confirm 节点，批处理零开销
    checkpointer = None
    initial_state = {
        "diff": diff,
        "mode": mode,
        "description": description,
        # #16 接线：被审仓库根路径进 state——#17（结构地图/import 邻域）
        # 与 #19（约定目录基准）从这里取
        "repo_root": str((source_repo or repo).resolve()),
    }
    start = time.monotonic()
    try:
        checkpointer = _make_checkpointer() if interactive else None
        graph = build_review_graph(
            provider,
            experts_enabled=config.experts.enabled,
            spec_sources=spec_sources,
            arm=arm,
            checkpointer=checkpointer,
            interactive=interactive,
        )
        if interactive:
            final = _invoke_interactive(graph, initial_state, repo)
        else:
            final = graph.invoke(initial_state)
    except _ConfirmAbort:
        # 用户选 q 中止：按中断语义返回（不产出报告，由调用方统一处理）
        raise
    except Exception as exc:
        raise ReviewerError(f"评审运行失败：{exc}") from exc
    finally:
        connection = getattr(checkpointer, "conn", None)
        try:
            if connection is not None:
                connection.close()
        finally:
            close_provider = getattr(provider, "close", None)
            if close_provider is not None:
                close_provider()
    duration_ms = round((time.monotonic() - start) * 1000)
    # #19：metadata.spec_kb 三键（additive，contract.py 注释）
    kb_state = final.get("spec_kb") or {}
    kb_documents = kb_state.get("documents") or []
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "summary": final["summary"],
        "findings": final["findings"],
        "metadata": _metadata(
            repo=repo,
            base_ref=base_ref,
            head_ref=head_ref,
            model=provider.model_name,
            duration_ms=duration_ms,
            description_source=description_source,
            no_changes=False,
            context_stats=final.get("context_stats"),  # #17：上下文预算统计
            spec_kb={
                "loaded": bool(kb_documents),
                "documents": len(kb_documents),
                "hash": kb_state.get("hash"),
            },
        ),
    }
    if source_commit:
        report["metadata"]["source_commit"] = source_commit
    report["metadata"]["enabled_experts"] = final.get("enabled_experts", [])
    report["metadata"]["arm"] = arm
    return report


def _empty_changes_report(
    *,
    mode: str,
    repo: Path,
    description_source: str,
    base_ref: str | None,
    head_ref: str | None,
) -> dict:
    """空 diff（无改动）短路：不进图、不调模型（也无需 API key），
    直接产出 pass 空报告；人类可读渲染据此提示「无改动」（render 读 no_changes）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "summary": {
            "verdict": "pass",
            "headline": "无改动：没有可审查的 diff",
            "counts": {"blocker": 0, "concern": 0, "nit": 0},
        },
        "findings": [],
        "metadata": _metadata(
            repo=repo,
            base_ref=base_ref,
            head_ref=head_ref,
            model="none",  # 短路零模型调用，不构造 provider
            duration_ms=0,
            description_source=description_source,
            no_changes=True,
        ),
    }


def _emit(report: dict, *, as_json: bool, show_nits: bool = False) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report, show_nits=show_nits))


def _cmd_check(args: argparse.Namespace, config: Config) -> int:
    return _run_command(args, config=config, default_mode="gatekeeper")


def _cmd_precheck(args: argparse.Namespace, config: Config) -> int:
    if not args.allow_network:
        if config.precheck.model.provider != "llama_cpp":
            raise ConfigError("默认预检只允许本地 llama_cpp；联网须显式传入 --allow-network")
        config = replace(config, model=config.precheck.model)
    return _run_command(args, config=config, default_mode="mentor")


def _run_command(args: argparse.Namespace, *, config: Config, default_mode: str) -> int:
    """check/precheck 共用入口：回放（--diff-file）优先，缺省走真实 git 输入（#16）。"""
    mode = _resolve_mode(
        explicit=args.mode, subcommand_default=default_mode, from_config=config.mode
    )
    # 描述显式来源二选一（#16）：同给 → 用法错误 64
    if args.description is not None and args.description_file is not None:
        print(
            "reviewer: --description 与 --description-file 二选一，不可同时给出",
            file=sys.stderr,
        )
        return EXIT_USAGE_ERROR
    # --repo（两种模式一致校验）：指向被审仓库/快照仓库，路径必须存在 → 否则 64
    if not args.repo.exists():
        print(f"reviewer: 仓库路径不存在：{args.repo}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # #19：spec 三层来源打包——--spec 可重复 / 逗号分隔展开为最高优先层，
    # config.spec_kb.paths 次之，约定目录由 repo_root 在加载器内推导。
    # 相对路径解析基准：回放 = 快照仓库（--repo），git = 进程 cwd（规格语义）。
    spec_cli = [
        path.strip() for raw in (args.spec or []) for path in raw.split(",") if path.strip()
    ]
    spec_base_dir = args.repo if args.diff_file is not None else Path.cwd()
    spec_sources = SpecSources(
        cli=spec_cli,
        config=list(config.spec_kb.paths),
        base_dir=str(spec_base_dir),
    )

    explicit_description: str | None = None
    source_commit: str | None = None
    if args.description is not None:
        explicit_description = args.description
    elif args.description_file is not None:
        try:
            explicit_description = args.description_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print(
                f"reviewer: 无法读取 UTF-8 描述文件 {args.description_file}：{exc}",
                file=sys.stderr,
            )
            return EXIT_USAGE_ERROR

    if args.diff_file is not None:
        # ---- 回放模式（评估地基）：--repo 指向被审代码的快照仓库 ----
        try:
            if str(args.diff_file) == "-":
                buffer = getattr(sys.stdin, "buffer", None)
                diff = buffer.read().decode("utf-8") if buffer is not None else sys.stdin.read()
            else:
                diff = args.diff_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print(f"reviewer: 无法读取 UTF-8 diff 文件 {args.diff_file}：{exc}", file=sys.stderr)
            return EXIT_USAGE_ERROR
        if explicit_description is not None:
            description, description_source = explicit_description, "explicit"
        else:
            description, description_source = "", "none"
        base_ref = head_ref = None  # 回放无 git 语义
    else:
        # ---- 真实 git 输入（#16）：采集失败（非 git 目录 / 引用不存在等
        #      输入侧问题）统一 → 用法错误 64 ----
        try:
            if args.command == "precheck":
                git_in = collect_precheck_diff(args.repo)
            else:
                git_in = collect_check_diff(args.repo, args.ref or config.base)
        except GitInputError as exc:
            print(f"reviewer: {exc}", file=sys.stderr)
            return EXIT_USAGE_ERROR
        diff = git_in.diff
        if explicit_description is not None:
            description, description_source = explicit_description, "explicit"
        else:
            # 缺省 best-effort：自动拼接 merge-base..HEAD 提交信息
            # （precheck 恒空，决策见 gitinput 模块 docstring）
            description = git_in.commits_text
            description_source = "commits" if description else "none"
        base_ref, head_ref = git_in.base_ref, git_in.head_ref
        source_commit = git_in.source_commit

    # 空 diff（git 无改动 / 回放空文件）→ 短路：pass 空报告，零模型调用
    if not diff.strip():
        report = _empty_changes_report(
            mode=mode,
            repo=args.repo,
            description_source=description_source,
            base_ref=base_ref,
            head_ref=head_ref,
        )
    else:
        source_view = (committed_snapshot(args.repo, source_commit)
                       if source_commit else nullcontext(args.repo))
        with source_view as source_repo:
            report = _run_review(
                diff=diff, mode=mode, description=description,
                description_source=description_source, repo=args.repo, config=config,
                base_ref=base_ref, head_ref=head_ref, spec_sources=spec_sources,
                arm=args.arm, interactive=args.interactive,
                source_repo=source_repo, source_commit=source_commit,
            )
    _emit(report, as_json=args.as_json, show_nits=args.show_nits)
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
    except (ConfigError, GitInputError) as exc:
        # 配置层错误（YAML/字段/密钥缺失，含 make_provider 构造期）→ 64；
        # 运行期模型调用失败走 ReviewerError → 70，两者语义区分。
        print(f"reviewer: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    except ReviewerError as exc:
        print(f"reviewer: {exc}", file=sys.stderr)
        return EXIT_SOFTWARE
    except _ConfirmAbort:
        # #23：--interactive 用户选 q 中止 → 中断退出码（无报告产出）
        print("reviewer: 确认已中止", file=sys.stderr)
        return EXIT_INTERRUPTED
    except KeyboardInterrupt:
        print("reviewer: 已中断", file=sys.stderr)
        return EXIT_INTERRUPTED
