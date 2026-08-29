"""扩容 round 2：拉取 5 个新候选的评审评论/commits → raw/。"""
import json
import subprocess
from pathlib import Path

GH = r"C:\Program Files\GitHub CLI\gh.exe"
OUT = Path(__file__).resolve().parent / "raw"
OUT.mkdir(exist_ok=True)

PRS = [
    ("T1", "crewAIInc/crewAI", 7117),
    ("T2", "langchain-ai/langgraph", 8598),
    ("T3", "getsentry/sentry-python", 7263),
    ("T4", "sqlfluff/sqlfluff", 8388),
    ("T5", "microsoft/autogen", 7054),
]


def api(path):
    r = subprocess.run([GH, "api", path], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


for sid, repo, n in PRS:
    data = {
        "id": sid, "repo": repo, "number": n,
        "pr": api(f"repos/{repo}/pulls/{n}"),
        "review_comments": api(f"repos/{repo}/pulls/{n}/comments") or [],
        "commits": [c["commit"]["message"].split("\n")[0]
                    for c in (api(f"repos/{repo}/pulls/{n}/commits") or [])],
    }
    (OUT / f"{sid}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(f"{sid} {repo}#{n}: {len(data['review_comments'])} review comments")
