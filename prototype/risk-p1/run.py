"""PROTOTYPE (throwaway) — Wayfinder 票 #9：业务逻辑一致性风险验证。

问题：只凭「结构地图 + diff + 变更文件新内容 + Spec KB」，单次强模型调用
能否发现真实形态（表面有防护、happy path 可跑通）的业务逻辑违规？

运行：python prototype/risk-p1/run.py   （需要环境变量 DEEPSEEK_API_KEY）
"""
import json
import os
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE / "sample-repo"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

SEVERITY_RANK = {"nit": 0, "concern": 1, "blocker": 2}


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def load_cases():
    cases = []
    for d in sorted((BASE / "cases").glob("case*.diff")):
        meta = json.loads(read(d.with_suffix(".expected.json")))
        meta["diff"] = read(d)
        meta["new_content"] = read(REPO / meta["file"])
        idx = meta["new_content"].index(meta["probe"])
        meta["expected_line"] = meta["new_content"][:idx].count("\n") + 1
        cases.append(meta)
    return cases


def build_prompts(case):
    system = f"""你是资深代码审查专家，负责「业务逻辑与规范一致性」审查维度。

## 仓库结构地图
{read(REPO / "README.md")}

## 规范（Spec KB）
{read(REPO / "specs" / "orders.md")}

## 严重度锚点
- blocker：功能错误 / 数据损坏 / 安全问题 / 必然崩溃，或明确违反 Spec KB 硬约束——不修不能合并
- concern：特定条件下可能出错、边界 / 健壮性缺失、可疑的业务逻辑偏差——需作者回应
- nit：风格、命名、可维护性——不阻塞

## 输出契约（严格 JSON，不要输出任何其它内容；无问题则 findings 为空数组）
{{"findings": [{{"file": "仓库相对路径", "line": 0, "severity": "blocker|concern|nit", "category": "architecture|logic|spec|style", "message": "一句话结论", "rationale": "为什么是问题：引用 specs/orders.md § 章节名或具体代码行为", "suggestion": "怎么改（可选）"}}]}}"""

    user = f"""## 变更 diff（unified）
```diff
{case["diff"]}
```

## 变更文件完整新内容（{case["file"]}）
```python
{case["new_content"]}
```

请审查这个改动。"""
    return system, user


def call_llm(system, user):
    key = os.environ["DEEPSEEK_API_KEY"]
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def score(case, findings):
    exp_file = case["file"]
    hit_idx = set()
    for i, f in enumerate(findings):
        got = str(f.get("file", "")).replace("\\", "/")
        same_file = got == exp_file or got.endswith("/" + exp_file)
        cites_rule = any(k in (f.get("rationale", "") + f.get("message", "")) for k in case["rule_kw"])
        if same_file and cites_rule:
            hit_idx.add(i)
    fp_idx = set(range(len(findings))) - hit_idx
    r = {
        "case": case["id"],
        "name": case["name"],
        "expect": {"file": exp_file, "line": case["expected_line"], "rule": case["rule_section"]},
        "detected": bool(hit_idx),
    }
    if hit_idx:
        best = max((findings[i] for i in hit_idx),
                   key=lambda f: SEVERITY_RANK.get(f.get("severity"), -1))
        try:
            line_off = int(best.get("line", -1)) - case["expected_line"]
        except (TypeError, ValueError):
            line_off = None
        r["hit"] = {
            "severity": best.get("severity"),
            "category": best.get("category"),
            "line_offset": line_off,
            "message": best.get("message"),
            "rationale": best.get("rationale"),
        }
    r["false_positives"] = [findings[i].get("message", str(findings[i])) for i in sorted(fp_idx)]
    return r


def main():
    cases = load_cases()
    print(f"PROTOTYPE risk-p1：{len(cases)} 个注入 case，模型 {MODEL}，temp=0\n")
    results = []
    for case in cases:
        t0 = time.time()
        system, user = build_prompts(case)
        raw = call_llm(system, user)
        try:
            findings = json.loads(raw)["findings"]
        except (json.JSONDecodeError, KeyError, TypeError):
            findings = [{"parse_error": raw[:500]}]
        r = score(case, findings)
        r["latency_s"] = round(time.time() - t0, 1)
        r["raw_findings_count"] = len(findings)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print("-" * 64)
    print("\n=== 汇总 ===")
    detected = sum(1 for r in results if r["detected"])
    print(f"检出 {detected}/{len(results)}")
    for r in results:
        mark = "HIT " if r["detected"] else "MISS"
        hit = r.get("hit", {})
        print(f"{mark} {r['case']} {r['name']} | sev={hit.get('severity', '-')} "
              f"cat={hit.get('category', '-')} line_off={hit.get('line_offset', '-')} "
              f"fp={len(r['false_positives'])} findings={r['raw_findings_count']} {r['latency_s']}s")


if __name__ == "__main__":
    main()
