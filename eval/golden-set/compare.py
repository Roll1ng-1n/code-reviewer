"""Wayfinder #11：专家团 vs 单 Agent 对照实验（配对差异，≥3 次重复）。

两臂唯一差异是分解：基线 = 一次调用跑四维；专家团 = 4 个专家（各自 charter +
指定 category）依次调用 + aggregator（重排 + 一次 LLM 合并去重 + 严重度只降不升）。
同模型、同上下文、同 schema、同 judge、同匹配协议（与 #10 完全相同）。

说明：judge 与被评模型同族（无第二供应商 key），如实记录；异族 judge 待未来替换。
"""
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
RUNS = 3

SEV = {"nit": 0, "concern": 1, "blocker": 2}
SEV_LIST = ["nit", "concern", "blocker"]

CONTRACT = """## 输出契约（严格 JSON，不要输出任何其它内容；无问题输出空数组）
{"findings": [{"file": "仓库相对路径（diff 头中的路径）", "line": 0, "severity": "blocker|concern|nit", "category": "architecture|logic|spec|style", "message": "一句话结论", "rationale": "为什么是问题，引用具体代码行为", "suggestion": "怎么改（可选）"}]}"""

CHARTER = {
    "architecture": "审查模块边界、依赖方向、接口契约——改动是否破坏现有结构；跨实现的一致性（公共契约放宽时各实现是否跟得上）。",
    "logic": "对照 PR 描述的意图，审查业务逻辑的正确性与遗漏；行为与文档声明是否一致；测试是否钉住行为边界。",
    "spec": "对照成文规范逐条检查违规。本仓库无成文规范库，直接输出空数组。",
    "style": "吸收 nit 级问题：命名、可维护性、文档与代码一致性、测试完备性。限定只出 nit 严重度。",
}

JUDGE = """你是代码审查评估专家。判断【候选 finding】与【参考标注 golden】是否描述同一底层问题。

判定标准：根因相同即同一问题；措辞、严重度、行号不同不影响判定。文件已确认相同。

【golden】{g}
【候选】{c}

输出（仅 JSON）：{{"same_underlying_issue": true 或 false, "reason": "≤30字"}}"""

MERGE = """多个专家审查了同一个 diff，以下 findings 可能描述重复问题。判断哪些描述同一底层问题并合并：
- 合并时保留最完整的描述，严重度取其中最高（blocker>concern>nit）
- 拿不准是否同一问题时，两条都保留
- 不新增、不删除任何非重复 findings，不改写内容

【findings】{fs}

输出（仅 JSON）：{{"findings": [...]}}"""


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


def norm(p):
    s = str(p).replace("\\", "/")
    for pre in ("a/", "b/"):
        if s.startswith(pre):
            s = s[2:]
    return s


def same_file(a, b):
    a, b = norm(a), norm(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def judge(g, f):
    try:
        v = json.loads(llm(JUDGE.format(
            g=json.dumps({k: g[k] for k in ("file", "severity", "category", "description")}, ensure_ascii=False),
            c=json.dumps(f, ensure_ascii=False)), "请判定。"))
        return v.get("same_underlying_issue") is True
    except (json.JSONDecodeError, KeyError):
        return False


def prf(findings, case):
    """匹配 golden，返回 (tp, fn, fp)。"""
    used, tp = set(), 0
    for g in case["golden"]:
        for i, f in enumerate(findings):
            if i in used or not same_file(f.get("file", ""), g["file"]):
                continue
            if judge(g, f):
                used.add(i)
                tp += 1
                break
    fn = len(case["golden"]) - tp
    fp = len(findings) - len(used)
    return tp, fn, fp


def arm_baseline(diff, desc):
    system = f"""你是资深代码审查专家，同时负责四个审查维度：
- 架构（architecture）：{CHARTER['architecture']}
- 业务逻辑（logic）：{CHARTER['logic']}
- 规范（spec）：{CHARTER['spec']}
- 风格（style）：{CHARTER['style']}

## 严重度锚点
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃，或明确的接口契约破坏——不修不能合并
- concern：特定条件下可能出错、边界 / 健壮性缺失、可疑的逻辑偏差、行为与文档声明不符——需作者回应
- nit：风格、命名、文档、测试完备性——不阻塞

只报告有把握的问题；宁缺毋滥。
{CONTRACT}"""
    raw = llm(system, f"## PR 描述\n{desc}\n\n## 变更 diff\n```diff\n{diff}\n```\n\n请审查这个改动。")
    try:
        return json.loads(raw)["findings"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def arm_panel(diff, desc):
    findings = []
    for cat, charter in CHARTER.items():
        sev_cap = "；限定只出 nit 严重度" if cat == "style" else ""
        system = f"""你是资深代码审查专家，本次只负责「{cat}」审查维度。

## 你的 Charter
{charter}

## 严重度锚点{sev_cap}
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃，或明确的接口契约破坏
- concern：特定条件下可能出错、边界缺失、可疑的逻辑偏差、行为与文档声明不符
- nit：风格、命名、文档、测试完备性

只报告有把握的问题；宁缺毋滥。category 一律填 "{cat}"。
{CONTRACT}"""
        raw = llm(system, f"## PR 描述\n{desc}\n\n## 变更 diff\n```diff\n{diff}\n```\n\n请审查这个改动（仅 {cat} 维度）。")
        try:
            findings += json.loads(raw)["findings"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    # aggregator：合并去重（一次 LLM 调用，严重度只降不升），再代码重排序
    if findings:
        raw = llm(MERGE.format(fs=json.dumps(findings, ensure_ascii=False)), "请合并去重。")
        try:
            findings = json.loads(raw)["findings"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    findings.sort(key=lambda f: (norm(f.get("file", "")),
                                 SEV.get(f.get("severity"), 9),
                                 str(f.get("line", 0))))
    return findings


def main():
    cases = json.loads((BASE / "golden.json").read_text(encoding="utf-8"))["cases"]
    print(f"对照实验：{len(cases)} case × 2 臂 × {RUNS} 次，模型={MODEL}（judge 同族）\n")
    arms = {"baseline": arm_baseline, "panel": arm_panel}
    all_runs = {}
    for arm_name, arm_fn in arms.items():
        tps, fns, fps = [], [], []
        for run in range(RUNS):
            tp = fn = fp = 0
            t0 = time.time()
            for c in cases:
                diff = (BASE / "cases" / f"{c['id']}.diff").read_text(encoding="utf-8")
                desc = (BASE / "cases" / f"{c['id']}.desc.txt").read_text(encoding="utf-8")
                t, n, p = prf(arm_fn(diff, desc), c)
                tp += t
                fn += n
                fp += p
            tps.append(tp)
            fns.append(fn)
            fps.append(fp)
            pr = tp / (tp + fp) if tp + fp else 0.0
            re_ = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * pr * re_ / (pr + re_) if pr + re_ else 0.0
            print(f"[{arm_name}] run{run+1}: TP={tp} FN={fn} FP={fp} "
                  f"P={pr:.2f} R={re_:.2f} F1={f1:.2f} ({time.time()-t0:.0f}s)")
        f1s = []
        for tp, fn, fp in zip(tps, fns, fps):
            p = tp / (tp + fp) if tp + fp else 0.0
            r = tp / (tp + fn) if tp + fn else 0.0
            f1s.append(2 * p * r / (p + r) if p + r else 0.0)
        all_runs[arm_name] = {"tps": tps, "fns": fns, "fps": fps, "f1s": f1s}

    b, p_ = all_runs["baseline"], all_runs["panel"]
    print("\n=== 配对差异 ===")
    for name, a in (("baseline", b), ("panel", p_)):
        f1m = statistics.mean(a["f1s"])
        f1s_ = statistics.stdev(a["f1s"]) if len(a["f1s"]) > 1 else 0.0
        print(f"{name:<9} TP mean±std = {statistics.mean(a['tps']):.1f}±{statistics.stdev(a['tps']) if len(a['tps'])>1 else 0:.1f}  "
              f"F1 mean±std = {f1m:.2f}±{f1s_:.2f}")
    diff_tp = [p - b for p, b in zip(p_["tps"], b["tps"])]
    diff_f1 = [p - b for p, b in zip(p_["f1s"], b["f1s"])]
    print(f"配对 ΔTP mean±std = {statistics.mean(diff_tp):+.1f}±{statistics.stdev(diff_tp) if len(diff_tp)>1 else 0:.1f}  "
          f"ΔF1 mean±std = {statistics.mean(diff_f1):+.2f}±{statistics.stdev(diff_f1) if len(diff_f1)>1 else 0:.2f}")

    out = BASE / "results"
    out.mkdir(exist_ok=True)
    (out / "compare-001.json").write_text(json.dumps({
        "model": MODEL, "runs": RUNS, "judge": MODEL + " (同族，无第二 key)",
        "baseline": b, "panel": p_,
        "paired_delta": {"tp_mean": statistics.mean(diff_tp), "f1_mean": statistics.mean(diff_f1)},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresults -> {out / 'compare-001.json'}")


if __name__ == "__main__":
    main()
