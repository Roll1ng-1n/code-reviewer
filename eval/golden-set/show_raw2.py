import json
from pathlib import Path

for sid in ["T1", "T2", "T3", "T4", "T5"]:
    d = json.loads(Path(f"eval/golden-set/raw/{sid}.json").read_text(encoding="utf-8"))
    pr = d["pr"]
    print(f"===== {sid} {d['repo']}#{d['number']}: {pr['title']}")
    print(f"files={pr['changed_files']} +{pr['additions']}/-{pr['deletions']}")
    for c in d["review_comments"]:
        body = c["body"][:400].replace("\n", " ")
        print(f"  [{c['user']['login']}] {c['path']}:{c.get('original_line') or c.get('line')} -> {body}")
    print("  commits:", " | ".join(d["commits"][:8]))
    print()
