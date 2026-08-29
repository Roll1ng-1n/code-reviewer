"""Wayfinder 票 #10：golden set 采集管线（Phase A 搜索 + Phase B 证据链探针）。

产出 eval/golden-set/shortlist.{json,md}：每条带 PR 链接、diff 规模、
评审评论摘要、修复 commit 证据链、分层提示（T1/T2/T3、问题类型）。
人工判定与标注由用户在 shortlist 上进行。
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

GH = r"C:\Program Files\GitHub CLI\gh.exe"
OUT = Path(__file__).resolve().parent

REPOS = [
    "langchain-ai/langgraph",
    "pydantic/pydantic",
    "crewAIInc/crewAI",
    "run-llama/llama_index",
    "microsoft/autogen",
    "pytest-dev/pytest",
    "sqlfluff/sqlfluff",
    "openai/openai-python",
]

TYPE_HINTS = [
    (("race", "concurrent", "idempotent", "atomic", "deadlock"), "logic"),
    (("edge case", "boundary", "off by", "empty", "null", "none"), "logic"),
    (("leak", "resource", "close", "connection"), "logic"),
    (("security", "injection", "sanitiz", "escape", "auth"), "logic"),
    (("api", "interface", "signature", "break", "deprecat", "contract"), "architecture"),
    (("naming", "style", "typo", "readab", "docstring", "import order"), "style"),
]

FIX_RE = re.compile(r"fix|address|correct|improve|refactor|handle|resolve|review|feedback", re.I)


def run_gh(*args):
    r = subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout


def gh_api(path):
    out = run_gh("api", path)
    return json.loads(out) if out else None


def search(repo, extra):
    out = run_gh("search", "prs", "--repo", repo, "--merged", *extra,
                 "--limit", "8", "--json", "number,title,commentsCount")
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def type_hint(text):
    t = text.lower()
    for kws, cat in TYPE_HINTS:
        if any(k in t for k in kws):
            return cat
    return "logic" if len(text) > 120 else "spec"


def probe(repo, n):
    pr = gh_api(f"repos/{repo}/pulls/{n}")
    if not pr or pr.get("additions") is None:
        return None
    total = pr["additions"] + pr["deletions"]
    diff_ok = total <= 800 and pr["changed_files"] <= 12

    comments = gh_api(f"repos/{repo}/pulls/{n}/comments") or []
    substantive = [c for c in comments if len(c.get("body", "")) >= 60]
    first_review_t = min((c["created_at"] for c in comments), default=None)
    rep = substantive[0]["body"].replace("\r", " ").replace("\n", " ") if substantive else ""

    commits = gh_api(f"repos/{repo}/pulls/{n}/commits") or []
    fix_commits = []
    for c in commits:
        msg = c["commit"]["message"].split("\n")[0]
        when = c["commit"]["author"]["date"]
        if first_review_t and when > first_review_t and FIX_RE.search(msg):
            fix_commits.append(msg)

    score = (2 * min(len(substantive), 3) + 2 * min(len(fix_commits), 2)
             + (1 if diff_ok else 0))
    evidence_ok = bool(substantive) and bool(fix_commits)
    hint_text = " ".join(c["body"] for c in substantive[:3])
    return {
        "repo": repo, "number": n, "url": f"https://github.com/{repo}/pull/{n}",
        "title": pr["title"], "additions": pr["additions"], "deletions": pr["deletions"],
        "changed_files": pr["changed_files"], "merged_at": pr.get("merged_at"),
        "review_comments": len(comments), "substantive": len(substantive),
        "fix_commits": fix_commits[:3], "representative_comment": rep[:200],
        "context_hint": "T3" if pr["changed_files"] >= 3 else "T2",
        "type_hint": type_hint(hint_text),
        "diff_ok": diff_ok, "evidence_ok": evidence_ok, "score": score,
    }


def main():
    candidates = []
    for repo in REPOS:
        hits = search(repo, ["--comments", ">2"])
        hits.sort(key=lambda h: -h["commentsCount"])
        print(f"[search] {repo}: {len(hits)} hits", file=sys.stderr)
        for h in hits[:5]:
            time.sleep(0.2)
            info = probe(repo, h["number"])
            if info:
                candidates.append(info)
                print(f"  probed #{h['number']} score={info['score']} "
                      f"ev={info['evidence_ok']}", file=sys.stderr)

    clean = []
    for repo in REPOS[:4]:
        for h in search(repo, ["--comments", "0"])[:2]:
            time.sleep(0.2)
            pr = gh_api(f"repos/{repo}/pulls/{h['number']}")
            if pr and pr.get("additions", 9999) + pr.get("deletions", 9999) <= 300:
                clean.append({
                    "repo": repo, "number": h["number"],
                    "url": f"https://github.com/{repo}/pull/{h['number']}",
                    "title": pr["title"], "additions": pr["additions"],
                    "deletions": pr["deletions"], "changed_files": pr["changed_files"],
                })

    shortlist = [c for c in candidates if c["evidence_ok"] and c["diff_ok"]]
    shortlist.sort(key=lambda c: -c["score"])
    shortlist = shortlist[:18]

    (OUT / "shortlist.json").write_text(
        json.dumps({"shortlist": shortlist, "clean_candidates": clean},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Golden set 候选 shortlist（人工判定用）", "",
             "勾选后进入标注。context_hint/type_hint 仅为分层提示，以你判断为准。", ""]
    for i, c in enumerate(shortlist, 1):
        lines += [
            f"- [ ] **S{i}** [{c['repo']}#{c['number']}]({c['url']}) {c['title']}",
            f"  - 规模 +{c['additions']}/-{c['deletions']}，{c['changed_files']} 文件；"
            f"分层提示 {c['context_hint']}/{c['type_hint']}",
            f"  - 评审摘录：{c['representative_comment'][:160]}",
            f"  - 修复证据：{'；'.join(c['fix_commits'][:2])}",
            "",
        ]
    lines += ["## 干净 PR 候选（对照组，测假阳性）", ""]
    for c in clean:
        lines.append(f"- [ ] [{c['repo']}#{c['number']}]({c['url']}) {c['title']} "
                     f"(+{c['additions']}/-{c['deletions']})")
    (OUT / "shortlist.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"shortlist: {len(shortlist)} 条；clean 候选 {len(clean)} 条", file=sys.stderr)


if __name__ == "__main__":
    main()
