"""Wayfinder #10：取每个 PR 的「评审者所见版本」diff（base...首提交），含修复的合并版不可用。"""
import json
import subprocess
from pathlib import Path

GH = r"C:\Program Files\GitHub CLI\gh.exe"
BASE = Path(__file__).resolve().parent
CASES = json.loads((BASE / "golden.json").read_text(encoding="utf-8"))["cases"]


def api(path, accept=None):
    args = [GH, "api"]
    if accept:
        args += ["-H", f"Accept: {accept}"]
    args.append(path)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


out = BASE / "cases"
out.mkdir(exist_ok=True)
for c in CASES:
    raw = json.loads((BASE / "raw" / f"{c['id']}.json").read_text(encoding="utf-8"))
    commits = json.loads(api(f"repos/{c['repo']}/pulls/{c['number']}/commits"))
    first_sha = commits[0]["sha"]
    base_sha = raw["pr"]["base"]["sha"]
    diff = api(f"repos/{c['repo']}/compare/{base_sha}...{first_sha}",
               accept="application/vnd.github.diff")
    if not diff:  # 回退：整个 PR 的合并 diff（干净 PR 多为单提交，等价）
        diff = api(f"repos/{c['repo']}/pulls/{c['number']}",
                   accept="application/vnd.github.diff") or ""
    (out / f"{c['id']}.diff").write_text(diff, encoding="utf-8")
    body = (raw["pr"].get("body") or "")[:800]
    (out / f"{c['id']}.desc.txt").write_text(
        c["title"] + "\n\n" + body, encoding="utf-8")
    print(f"{c['id']}: {len(diff)} bytes (first commit {first_sha[:7]})")
print("done ->", out)
