"""Wayfinder #11 对照实验的产品化（实现 10/11 #22）：产品 CLI 驱动的配对比较。

对比 ``compare.py``（内联双臂——arm_baseline/arm_panel 直接在脚本里拼 prompt 调
DeepSeek）：本脚本改为**驱动产品 CLI**（Replay 模式）跑 golden set——每 case、
每臂、每重复用 subprocess 调 ``python -m reviewer check --diff-file ...
--description-file ... --arm <panel|baseline> --json``，解析 Report.findings，
再走 evaluate.py 同款 judge 语义匹配（文件硬约束 + judge 一对一 + 同族 judge
如实标注）。拓扑结论此后始终对着真实产品测量。

两臂唯一差异是分解（专家团 = 四专家并行；基线 = 单一融合专家一次调用），
共享模型 / 上下文 / schema / aggregate（含复核过滤，FP 控制层对两臂同等施加——
比 #11 更严格地隔离「分解」这个唯一变量）。

**parity 配置说明**：隔离 cwd + 明确模型配置 + 显式空 ``--repo`` 快照，
只有 diff + 描述，无结构地图、依赖或自动规范。
judge 与被评模型同族（deepseek-chat，无第二供应商 key），如实记录为已知限制。

运行：python eval/golden-set/compare_product.py --runs 3   （需 DEEPSEEK_API_KEY）
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from reviewer.contract import Finding, derive_verdict, exit_code_for

BASE = Path(__file__).resolve().parent
# 只用于定位产品 Python 源码；不得用作实验 cwd 或快照。
REPO_ROOT = Path(__file__).resolve().parents[2]

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
DEFAULT_RUNS = 3
JUDGE_MODEL = "deepseek-chat"


class EvaluationError(RuntimeError):
    """Infrastructure or recipe failure; never an empty successful review."""


def evaluation_config(model: str) -> dict:
    return {"model": {"provider": "deepseek", "name": model,
                      "api_key_env": "DEEPSEEK_API_KEY"},
            "spec_kb": {"paths": []},
            "experts": {"enabled": ["architecture", "logic", "spec", "style"]}}


@contextmanager
def evaluation_workspace(model: str):
    with tempfile.TemporaryDirectory(prefix="reviewer-eval-") as directory:
        cwd = Path(directory)
        (cwd / "snapshot").mkdir()
        (cwd / ".reviewer.yaml").write_text(json.dumps(evaluation_config(model)), encoding="utf-8")
        yield cwd

# 与 evaluate.py 同款的 judge 语义匹配协议（#4 调研：文件硬约束 + LLM judge
# 一对一 + 同族 judge 如实标注）
JUDGE = """你是代码审查评估专家。判断【候选 finding】与【参考标注 golden】是否描述同一底层问题。

判定标准：根因相同即同一问题；措辞、严重度、行号不同不影响判定。文件已确认相同。

【golden】{g}
【候选】{c}

输出（仅 JSON）：{{"same_underlying_issue": true 或 false, "reason": "≤30字"}}"""


def llm(system: str, user: str) -> str:
    """judge 用 LLM 调用（同族 deepseek-chat，标准库 urllib）。"""
    key = os.environ["DEEPSEEK_API_KEY"]
    body = json.dumps(
        {
            "model": JUDGE_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"][
            "content"
        ]


def norm(path: object) -> str:
    """路径归一化（去 a/ b/ 前缀、反斜杠转正斜杠），与 evaluate.py 同款。"""
    s = str(path).replace("\\", "/")
    for prefix in ("a/", "b/"):
        if s.startswith(prefix):
            s = s[2:]
    return s


def same_file(a: object, b: object) -> bool:
    """文件硬约束（后缀匹配容错），与 evaluate.py 同款。"""
    a, b = norm(a), norm(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def judge(golden: dict, finding: dict) -> bool:
    """judge 一对一：候选 finding 是否与 golden 描述同一底层问题。"""
    try:
        v = json.loads(
            llm(
                JUDGE.format(
                    g=json.dumps(
                        {k: golden[k] for k in ("file", "severity", "category", "description")},
                        ensure_ascii=False,
                    ),
                    c=json.dumps(finding, ensure_ascii=False),
                ),
                "请判定。",
            )
        )
        if not isinstance(v, dict) or type(v.get("same_underlying_issue")) is not bool:
            raise ValueError("judge 缺少布尔判决 same_underlying_issue")
        return v["same_underlying_issue"]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise EvaluationError(f"judge 响应无效：{exc}") from exc


def match(case: dict, findings: list[dict]) -> tuple[int, int, int]:
    """文件硬约束 + judge 一对一匹配，返回 (tp, fn, fp)。

    clean case（golden 空）→ findings 全算 FP；非 clean → 逐 golden judge 匹配。
    """
    if case["clean"]:
        return 0, 0, len(findings)
    used: set[int] = set()
    tp = 0
    for g in case["golden"]:
        candidates = [
            (i, f)
            for i, f in enumerate(findings)
            if i not in used and same_file(f.get("file", ""), g["file"])
        ]
        for i, f in candidates:
            if judge(g, f):
                used.add(i)
                tp += 1
                break
    fn = len(case["golden"]) - tp
    fp = len(findings) - len(used)
    return tp, fn, fp


def _validate_report(report: object, returncode: int, model: str) -> list[dict]:
    if not isinstance(report, dict) or report.get("schema_version") != "1":
        raise EvaluationError("缺少或非法 schema-v1 Report")
    if report.get("mode") != "gatekeeper":
        raise EvaluationError("Report.mode 无效")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise EvaluationError("Report.findings 必须是数组")
    for i, item in enumerate(findings, 1):
        Finding.model_validate(item, strict=True)
        if item.get("id") != f"F{i:03d}":
            raise EvaluationError("Report.findings 编号无效")
    summary = report.get("summary")
    expected = derive_verdict(findings)
    counts = summary.get("counts") if isinstance(summary, dict) else None
    if (not isinstance(summary, dict) or not isinstance(summary.get("headline"), str)
            or not isinstance(counts, dict) or counts != expected["counts"]
            or any(type(value) is not int for value in counts.values())
            or summary.get("verdict") != expected["verdict"]
            or returncode != exit_code_for(expected["verdict"])):
        raise EvaluationError("Report.summary / Findings / 退出码不一致")
    metadata = report.get("metadata")
    required = {"repo", "base_ref", "head_ref", "model", "spec_kb", "duration_ms", "timestamp"}
    if not isinstance(metadata, dict) or not required.issubset(metadata):
        raise EvaluationError("Report.metadata 缺失必要字段")
    if (not isinstance(metadata["repo"], str)
            or type(metadata["duration_ms"]) is not int or metadata["duration_ms"] < 0
            or not isinstance(metadata["timestamp"], str)):
        raise EvaluationError("Report.metadata 字段无效")
    datetime.fromisoformat(metadata["timestamp"])
    if metadata["model"] != model:
        raise EvaluationError(f"被评模型不符：要求 {model}，实际 {metadata['model']}")
    stats = metadata.get("context_stats")
    if (metadata["spec_kb"] != {"loaded": False, "documents": 0, "hash": None}
            or not isinstance(stats, dict) or stats.get("structure_map_lines") != 0
            or stats.get("neighborhood_files") != 0
            or metadata.get("no_changes") or metadata["base_ref"] is not None
            or metadata["head_ref"] is not None):
        raise EvaluationError("Report 不符合 diff+description 空快照配方")
    return findings


def run_arm_case(arm: str, case: dict, *, model: str = MODEL, workspace: Path | None = None) -> list[dict]:
    """Isolated Replay; valid empty Findings succeed, execution failures raise."""
    if workspace is None:
        with evaluation_workspace(model) as isolated:
            return run_arm_case(arm, case, model=model, workspace=isolated)
    diff = BASE / "cases" / f"{case['id']}.diff"
    desc = BASE / "cases" / f"{case['id']}.desc.txt"
    cmd = [
        sys.executable,
        "-m",
        "reviewer",
        "check",
        "--diff-file",
        str(diff),
        "--description-file",
        str(desc),
        "--arm",
        arm,
        "--repo",
        str(workspace / "snapshot"),
        "--json",
    ]
    # 强制子进程 stdout/stderr 用 utf-8（Windows 下默认 GBK 会与 --json 中文
    # 冲突）；errors="replace" 兜底避免解码崩溃（warning 中文属非关键输出）。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        cmd,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=600,
    )
    if proc.returncode not in (0, 1, 2):
        raise EvaluationError(f"CLI 退出码 {proc.returncode}：{proc.stderr.strip()[:200]}")
    try:
        report = json.loads(proc.stdout)
        return _validate_report(report, proc.returncode, model)
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"非法产品 Report：{exc}") from exc


def f1_of(tp: int, fn: int, fp: int) -> float:
    """由 (tp, fn, fp) 计算 F1。"""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="golden set 配对比较（产品 CLI 驱动）"
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="每臂重复次数")
    parser.add_argument("--model", default=MODEL, help="明确指定被评模型；Report 必须与之相符")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs 必须至少为 1")

    cases = json.loads((BASE / "golden.json").read_text(encoding="utf-8"))["cases"]
    print(
        f"配对比较（产品 CLI）：{len(cases)} case × 2 臂 × {args.runs} 次，"
        f"被评模型={args.model}；judge={JUDGE_MODEL}\n"
    )

    arms = ["panel", "baseline"]
    all_runs: dict[str, dict[str, list[float]]] = {}
    for arm in arms:
        tps: list[int] = []
        fns: list[int] = []
        fps: list[int] = []
        f1s: list[float] = []
        for run in range(args.runs):
            tp = fn = fp = 0
            t0 = time.time()
            for case in cases:
                try:
                    findings = run_arm_case(arm, case, model=args.model)
                    t, n, p = match(case, findings)
                except Exception as exc:
                    print(f"评估失败：case={case['id']} arm={arm} run={run + 1}：{exc}", file=sys.stderr)
                    return 1
                tp += t
                fn += n
                fp += p
            tps.append(tp)
            fns.append(fn)
            fps.append(fp)
            f1s.append(f1_of(tp, fn, fp))
            print(
                f"[{arm}] run{run + 1}: TP={tp} FN={fn} FP={fp} "
                f"F1={f1s[-1]:.2f} ({time.time() - t0:.0f}s)"
            )
        all_runs[arm] = {"tps": tps, "fns": fns, "fps": fps, "f1s": f1s}

    panel = all_runs["panel"]
    baseline = all_runs["baseline"]

    print("\n=== 配对差异 ===")
    for arm, runs in (("panel", panel), ("baseline", baseline)):
        tp_mean = statistics.mean(runs["tps"])
        tp_std = statistics.stdev(runs["tps"]) if len(runs["tps"]) > 1 else 0.0
        f1_mean = statistics.mean(runs["f1s"])
        f1_std = statistics.stdev(runs["f1s"]) if len(runs["f1s"]) > 1 else 0.0
        print(
            f"{arm:<9} TP mean±std = {tp_mean:.1f}±{tp_std:.1f}  "
            f"F1 mean±std = {f1_mean:.2f}±{f1_std:.2f}"
        )
    diff_tp = [p - b for p, b in zip(panel["tps"], baseline["tps"])]
    diff_f1 = [p - b for p, b in zip(panel["f1s"], baseline["f1s"])]
    tp_mean = statistics.mean(diff_tp)
    tp_std = statistics.stdev(diff_tp) if len(diff_tp) > 1 else 0.0
    f1_mean = statistics.mean(diff_f1)
    f1_std = statistics.stdev(diff_f1) if len(diff_f1) > 1 else 0.0
    print(
        f"配对 ΔTP mean±std = {tp_mean:+.1f}±{tp_std:.1f}  "
        f"ΔF1 mean±std = {f1_mean:+.2f}±{f1_std:.2f}"
    )

    out = BASE / "results"
    out.mkdir(exist_ok=True)
    result_path = out / "compare-product-001.json"
    result_text = json.dumps(
            {
                "model": args.model,
                "evaluated_model": args.model,
                "judge_model": JUDGE_MODEL,
                "configuration": evaluation_config(args.model),
                "recipe": "diff+description",
                "runs": args.runs,
                "judge": JUDGE_MODEL,
                "driver": "product-cli",
                "arm": {"panel": "四专家并行", "baseline": "单 Agent 融合专家"},
                "parity_note": "隔离 cwd + 明确配置 + 显式空 --repo；两臂只有 diff+描述",
                "panel": panel,
                "baseline": baseline,
                "paired_delta": {
                    "tp_mean": tp_mean,
                    "tp_std": tp_std,
                    "f1_mean": f1_mean,
                    "f1_std": f1_std,
                },
            },
            ensure_ascii=False,
            indent=2,
    )
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out,
                                         prefix=".compare-product-", suffix=".tmp", delete=False) as file:
            pending = Path(file.name)
            file.write(result_text)
        pending.replace(result_path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
    print(f"\nresults -> {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
