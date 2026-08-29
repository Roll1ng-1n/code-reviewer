"""Wayfinder #10 扩容 round 2：定向补 T3（≥3 文件）/疑似 blocker 样本。"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

GH = r"C:\Program Files\GitHub CLI\gh.exe"
OUT = Path(__file__).resolve().parent

REPOS = [
    "encode/httpx", "pallets/flask", "encode/starlette",
    "langflow-ai/langflow", "run-llama/llama_index",
    "microsoft/autogen", "crewAIInc/crewAI", "openai/openai-python",
    "pallets/werkzeug", "encode/uvicorn",
    "langchain-ai/langgraph", "getsentry/sentry-python",
    "sqlfluff/sqlfluff", "pytest-dev/pytest",
]
FIX_RE = re.compile(r"fix|address|correct|improve|refactor|handle|resolve|review|feedback|rename|update|comment", re.I)
BUG_RE = re.compile(r"bug|crash|error|fail|broke|regression|race|leak|deadlock|incorrect|wrong", re.I)


def run_gh(*args):
    r = subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", timeout=60)
    return r.stdout if r.returncode == 0 else None


def gh_api(path):
    out = run_gh("api", path)
    return json.loads(out) if out else None


def search(repo, extra):
    out = run_gh("search", "prs", "--repo", repo, "--merged", *extra,
                 "--limit", "6", "--json", "number,title,commentsCount")
    try:
        return json.loads(out) if out else []
    except json.JSONDecodeError:
        return []


def probe(repo, n):
    pr = gh_api(f"repos/{repo}/pulls/{n}")
    if not pr or pr.get("additions") is None:
        return None
    total = pr["additions"] + pr["deletions"]
    if total > 1200 or pr["changed_files"] < 2 or pr["changed_files"] > 14:
        return None
    comments = gh_api(f"repos/{repo}/pulls/{n}/comments") or []
    substantive = [c for c in comments if len(c.get("body", "")) >= 60]
    if not substantive:
        return None
    first_t = min((c["created_at"] for c in comments), default=None)
    commits = gh_api(f"repos/{repo}/pulls/{n}/commits") or []
    fixes = [c["commit"]["message"].split("\n")[0] for c in commits
             if first_t and c["commit"]["author"]["date"] > first_t and FIX_RE.search(c["commit"]["message"])]
    if not fixes:
        return None
    rep = substantive[0]["body"].replace("\r", " ").replace("\n", " ")
    bug_hint = bool(BUG_RE.search((pr["title"] or "") + " " + rep[:300]))
    score = min(len(substantive), 3) * 2 + min(len(fixes), 2) + (3 if bug_hint else 0) + (2 if pr["changed_files"] >= 4 else 0)
    return {"repo": repo, "number": n, "url": f"https://github.com/{repo}/pull/{n}",
            "title": pr["title"], "additions": pr["additions"], "deletions": pr["deletions"],
            "changed_files": pr["changed_files"], "substantive": len(substantive),
            "fixes": fixes[:3], "rep": rep[:200], "bug_hint": bug_hint, "score": score}


def main():
    out = []
    for repo in REPOS:
        hits = search(repo, ["--comments", ">2"])
        hits.sort(key=lambda h: -h["commentsCount"])
        print(f"[search] {repo}: {len(hits)}", file=sys.stderr)
        for h in hits[:4]:
            time.sleep(0.2)
            info = probe(repo, h["number"])
            if info:
                out.append(info)
                print(f"  #{h['number']} score={info['score']} bug={info['bug_hint']} files={info['changed_files']}", file=sys.stderr)
    out.sort(key=lambda c: -c["score"])
    out = out[:10]
    (OUT / "shortlist2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in out:
        print(f"S2-{c['repo'].split('/')[-1]}#{c['number']} [{c['score']}] files={c['changed_files']} bug={c['bug_hint']} {c['title'][:70]}", file=sys.stderr)


if __name__ == "__main__":
    main()
