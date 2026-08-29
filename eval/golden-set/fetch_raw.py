"""Wayfinder 票 #10：拉取选定 PR 的评审评论/commits/元数据 → raw/<id>.json（标注底稿）。"""
import json
import subprocess
from pathlib import Path

GH = r"C:\Program Files\GitHub CLI\gh.exe"
OUT = Path(__file__).resolve().parent / "raw"
OUT.mkdir(exist_ok=True)

PRS = [
    ("S1", "langchain-ai/langgraph", 8617),
    ("S3", "langchain-ai/langgraph", 8569),
    ("S4", "langchain-ai/langgraph", 8526),
    ("S8", "pydantic/pydantic", 13717),
    ("S6", "microsoft/autogen", 7521),
    ("C1", "langchain-ai/langgraph", 8595),
    ("C2", "pydantic/pydantic", 8425),
    ("C3", "crewAIInc/crewAI", 5747),
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
print("saved to", OUT)
