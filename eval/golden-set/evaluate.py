"""Wayfinder #10：golden set 评估器——一条命令算「模型输出 vs 标注」的 P/R。

协议（来自调研 #4）：文件硬约束 + LLM judge 语义等价一对一匹配 + 行号 ±3 分层信号。
臂：单 Agent 基线（#8 定义：四维 charter 合一的单次调用）。
运行：python eval/golden-set/evaluate.py   （需 DEEPSEEK_API_KEY）
"""
import json
import os
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

SYSTEM = """你是资深代码审查专家，同时负责四个审查维度：
- 架构（architecture）：模块边界、依赖方向、接口契约——改动是否破坏现有结构
- 业务逻辑（logic）：对照下方 PR 描述的意图，审查逻辑正确性与遗漏
- 规范（spec）：本仓库无成文规范库，此维度跳过
- 风格（style）：命名、可维护性、文档与测试的小问题

## 严重度锚点
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃，或明确的接口契约破坏——不修不能合并
- concern：特定条件下可能出错、边界 / 健壮性缺失、可疑的逻辑偏差、行为与文档声明不符——需作者回应
- nit：风格、命名、文档、测试完备性——不阻塞

只报告有把握的问题；宁缺毋滥。无问题输出空数组。

## 输出契约（严格 JSON，不要输出任何其它内容）
{"findings": [{"file": "仓库相对路径（diff 头中的路径）", "line": 0, "severity": "blocker|concern|nit", "category": "architecture|logic|spec|style", "message": "一句话结论", "rationale": "为什么是问题，引用具体代码行为", "suggestion": "怎么改（可选）"}]}"""

JUDGE = """你是代码审查评估专家。判断【候选 finding】与【参考标注 golden】是否描述同一底层问题。

判定标准：根因相同即同一问题；措辞、严重度、行号不同不影响判定。文件已确认相同。

【golden】{g}
【候选】{c}

输出（仅 JSON）：{{"same_underlying_issue": true 或 false, "reason": "≤30字"}}"""


def norm(p):
    s = str(p).replace("\\", "/")
    for pre in ("a/", "b/"):
        if s.startswith(pre):
            s = s[2:]
    return s


def same_file(a, b):
    a, b = norm(a), norm(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def llm(system, user):
    key = os.environ["DEEPSEEK_API_KEY"]
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]


def run_case(c):
    diff = (BASE / "cases" / f"{c['id']}.diff").read_text(encoding="utf-8")
    desc = (BASE / "cases" / f"{c['id']}.desc.txt").read_text(encoding="utf-8")
    user = f"## PR 描述\n{desc}\n\n## 变更 diff（unified）\n```diff\n{diff}\n```\n\n请审查这个改动。"
    raw = llm(SYSTEM, user)
    try:
        return json.loads(raw)["findings"], raw
    except (json.JSONDecodeError, KeyError, TypeError):
        return [{"parse_error": raw[:300]}], raw


def match(c, findings):
    """文件硬约束 + judge 一对一匹配。返回 (matched {gid: finding}, fp_list)。"""
    used, matched = set(), {}
    for g in c["golden"]:
        cands = [(i, f) for i, f in enumerate(findings)
                 if i not in used and same_file(f.get("file", ""), g["file"])]
        for i, f in cands:
            try:
                v = json.loads(llm(
                    JUDGE.format(
                        g=json.dumps({k: g[k] for k in ("file", "severity", "category", "description")}, ensure_ascii=False),
                        c=json.dumps(f, ensure_ascii=False)),
                    "请判定上述两者是否描述同一底层问题。"))
                if v.get("same_underlying_issue") is True:
                    matched[g["id"]] = f
                    used.add(i)
                    break
            except (json.JSONDecodeError, KeyError):
                continue
    fps = [f for i, f in enumerate(findings) if i not in used]
    return matched, fps


def main():
    cases = json.loads((BASE / "golden.json").read_text(encoding="utf-8"))["cases"]
    print(f"评估 golden set：{len(cases)} 个 case（{sum(1 for c in cases if not c['clean'])} change + "
          f"{sum(1 for c in cases if c['clean'])} clean），臂=单 Agent 基线，模型={MODEL}\n")
    tp = fn = fp = 0
    per_case, clean_fps = [], []
    for c in cases:
        t0 = time.time()
        findings, raw = run_case(c)
        entry = {"id": c["id"], "url": c["url"], "n_findings": len(findings),
                 "latency_s": round(time.time() - t0, 1)}
        if c["clean"]:
            entry["false_positives"] = [f.get("message", "?") for f in findings]
            entry["fp_detail"] = findings
            clean_fps += findings
            fp += len(findings)
            status = f"CLEAN fp={len(findings)}"
        else:
            matched, fps = match(c, findings)
            entry["matched"] = {g: {"line": f.get("line"), "severity": f.get("severity"),
                                    "message": f.get("message")}
                                for g, f in matched.items()}
            entry["missed_golden"] = [g["id"] for g in c["golden"] if g["id"] not in matched]
            entry["false_positives"] = [f.get("message", "?") for f in fps]
            tp += len(matched)
            fn += len(c["golden"]) - len(matched)
            fp += len(fps)
            status = f"TP={len(matched)}/{len(c['golden'])} FP={len(fps)}"
        per_case.append(entry)
        print(f"{c['id']:<3} {status:<22} findings={len(findings)} {entry['latency_s']}s")

    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    print("\n=== 汇总（语义匹配） ===")
    print(f"TP={tp}  FN={fn}  FP={fp}  →  P={p:.2f}  R={r:.2f}  F1={f1:.2f}")
    print(f"干净 PR 假阳性：{len(clean_fps)} 条")
    for e in per_case:
        if e.get("missed_golden"):
            print(f"  漏报 {e['id']}: {', '.join(e['missed_golden'])}")

    out = BASE / "results"
    out.mkdir(exist_ok=True)
    (out / "run-001.json").write_text(
        json.dumps({"model": MODEL, "arm": "single-agent-baseline",
                    "precision": p, "recall": r, "f1": f1,
                    "tp": tp, "fn": fn, "fp": fp, "clean_fp": len(clean_fps),
                    "per_case": per_case}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\nresults -> {out / 'run-001.json'}")


if __name__ == "__main__":
    main()
