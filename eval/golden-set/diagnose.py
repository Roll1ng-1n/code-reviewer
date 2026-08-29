"""Wayfinder #11：failure mode 诊断——两臂逐 finding 对比 + aggregator 损耗检查。

复跑 1 次两臂（各 8 case），输出：① 每臂的逐 case TP/FP/missed；② 两臂独有 TP
对比；③ 专家团「合并前 vs 合并后」的 finding 数差异（aggregator 损耗）。
"""
import json
import os
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
SEV = {"nit": 0, "concern": 1, "blocker": 2}

CONTRACT = """## 输出契约（严格 JSON，不要输出任何其它内容；无问题输出空数组）
{"findings": [{"file": "...", "line": 0, "severity": "blocker|concern|nit", "category": "architecture|logic|spec|style", "message": "...", "rationale": "...", "suggestion": "..."}]}"""
CHARTER = {
    "architecture": "审查模块边界、依赖方向、接口契约——改动是否破坏现有结构；跨实现的一致性。",
    "logic": "对照 PR 描述意图，审查逻辑正确性与遗漏；行为与文档声明是否一致；测试是否钉住边界。",
    "spec": "本仓库无成文规范库，直接输出空数组。",
    "style": "命名、可维护性、文档与代码一致性、测试完备性。限定只出 nit。",
}
JUDGE = '判断两者是否同一底层问题。根因相同即同一问题。【golden】{g}\n【候选】{c}\n输出（仅 JSON）：{{"same_underlying_issue": true 或 false}}'
MERGE = '合并以下 findings 中描述同一底层问题的重复项，保留最完整描述、严重度取最高；拿不准则都保留；不新增不删除。【findings】{fs}\n输出（仅 JSON）：{{"findings": [...]}}'


def llm(sys_, user):
    key = os.environ["DEEPSEEK_API_KEY"]
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": sys_}, {"role": "user", "content": user}],
        "temperature": 0, "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def norm(p):
    s = str(p).replace("\\", "/")
    return s[2:] if s[:2] in ("a/", "b/") else s


def same_file(a, b):
    a, b = norm(a), norm(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def judge(g, f):
    try:
        return json.loads(llm(JUDGE.format(
            g=json.dumps({k: g[k] for k in ("file", "severity", "description")}, ensure_ascii=False),
            c=json.dumps(f, ensure_ascii=False)), "判定。")).get("same_underlying_issue") is True
    except Exception:
        return False


def arm_baseline(diff, desc):
    raw = llm("你是资深审查专家，负责 architecture/logic/style 三维。"
              "只报告有把握的问题。\n" + CONTRACT,
              f"## PR 描述\n{desc}\n\n## diff\n```diff\n{diff}\n```")
    try:
        return json.loads(raw)["findings"]
    except Exception:
        return []


def arm_panel(diff, desc):
    fs = []
    for cat, ch in CHARTER.items():
        cap = "只出 nit。" if cat == "style" else ""
        raw = llm(f"你只负责「{cat}」维度：{ch} {cap}\n{CONTRACT}",
                  f"## PR 描述\n{desc}\n\n## diff\n```diff\n{diff}\n```\n仅审查 {cat} 维度。")
        try:
            fs += json.loads(raw)["findings"]
        except Exception:
            pass
    pre = len(fs)
    merged = fs
    if fs:
        try:
            merged = json.loads(llm(MERGE.format(fs=json.dumps(fs, ensure_ascii=False)), "合并。"))["findings"]
        except Exception:
            pass
    merged.sort(key=lambda f: (norm(f.get("file", "")), SEV.get(f.get("severity"), 9)))
    return merged, pre


def main():
    cases = json.loads((BASE / "golden.json").read_text(encoding="utf-8"))["cases"]
    print("failure mode 诊断（单跑，两臂各 8 case）\n")
    for c in cases:
        diff = (BASE / "cases" / f"{c['id']}.diff").read_text(encoding="utf-8")
        desc = (BASE / "cases" / f"{c['id']}.desc.txt").read_text(encoding="utf-8")
        b = arm_baseline(diff, desc)
        p, pre = arm_panel(diff, desc)
        b_tp = [g["id"] for g in c["golden"] if any(judge(g, f) for f in b)]
        p_tp = [g["id"] for g in c["golden"] if any(judge(g, f) for f in p)]
        only_p = set(p_tp) - set(b_tp)
        only_b = set(b_tp) - set(p_tp)
        print(f"{c['id']}: baseline TP={b_tp or '无'} FP={len(b)-len(b_tp)} | "
              f"panel TP={p_tp or '无'} FP={len(p)-len(p_tp)} (合并前 {pre}→{len(p)})")
        if only_p:
            print(f"   专家团独有 TP: {sorted(only_p)}")
        if only_b:
            print(f"   基线独有 TP: {sorted(only_b)}")


if __name__ == "__main__":
    main()
