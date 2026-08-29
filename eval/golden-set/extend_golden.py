"""扩容 round 2：向 golden.json 追加 5 个新 case（6 条标注）。"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
p = BASE / "golden.json"
data = json.loads(p.read_text(encoding="utf-8"))

NEW = [
    {
        "id": "T1",
        "repo": "crewAIInc/crewAI",
        "number": 7117,
        "url": "https://github.com/crewAIInc/crewAI/pull/7117",
        "title": "feat(events): report machine size as a coarse band, not a core count",
        "clean": False,
        "golden": [
            {
                "id": "T1-F1",
                "file": "docs/edge/en/telemetry.mdx",
                "line": 64,
                "severity": "concern",
                "category": "logic",
                "context_type": "T1",
                "description": "文档声称 cpu_band 来自环境变量，但代码实际使用 os.cpu_count()——行为与文档声明不符（同 PR 内代码与文档均在 diff 中）",
                "evidence": "https://github.com/crewAIInc/crewAI/pull/7117 coderabbit 评论 + fix commit 'docs(telemetry): say where the cpu band comes from'",
            }
        ],
    },
    {
        "id": "T2",
        "repo": "langchain-ai/langgraph",
        "number": 8598,
        "url": "https://github.com/langchain-ai/langgraph/pull/8598",
        "title": "feat(sdk-py): add decrypt replacement result",
        "clean": False,
        "golden": [
            {
                "id": "T2-F1",
                "file": "libs/sdk-py/langgraph_sdk/encryption/types.py",
                "line": 82,
                "severity": "concern",
                "category": "architecture",
                "context_type": "T2",
                "description": "BlobDecryptor 改为联合返回类型会经装饰器 @encryption.decrypt.blob 传播，加宽所有被装饰 handler 的返回类型，破坏下游类型精度——需用 bounded TypeVar 保持具体返回类型",
                "evidence": "https://github.com/langchain-ai/langgraph/pull/8598 open-swe 评论 + fix commit 'fix(sdk): preserve decrypt handler types' (9dde905d)",
            }
        ],
    },
    {
        "id": "T3",
        "repo": "getsentry/sentry-python",
        "number": 7263,
        "url": "https://github.com/getsentry/sentry-python/pull/7263",
        "title": "chore: Drop more deprecated stuff",
        "clean": False,
        "golden": [
            {
                "id": "T3-F1",
                "file": "sentry_sdk/transport.py",
                "line": 1167,
                "severity": "blocker",
                "category": "logic",
                "context_type": "T3",
                "description": "删除函数式 transport 后，传 callable 的调用方不再被识别，make_transport 回退构造默认 HttpTransport——测试中传 no-op/list-append 函数的调用方将静默发送真实事件（High Severity，静默行为回归）",
                "evidence": "https://github.com/getsentry/sentry-python/pull/7263 cursor[bot] High Severity 评论 + 维护者确认 + fix commit 'remove callable from transport types' (c52f9556)",
            }
        ],
    },
    {
        "id": "T4",
        "repo": "sqlfluff/sqlfluff",
        "number": 8388,
        "url": "https://github.com/sqlfluff/sqlfluff/pull/8388",
        "title": "Clickhouse: support CONSTRAINT ... CHECK/ASSUME table constraints",
        "clean": False,
        "golden": [
            {
                "id": "T4-F1",
                "file": "src/sqlfluff/dialects/dialect_clickhouse.py",
                "line": 1576,
                "severity": "nit",
                "category": "style",
                "context_type": "T1",
                "description": "CHECK 与 ASSUME 分支仅差一个关键字，重复的 Sequence(..., Ref(ExpressionSegment)) 嵌套可用 OneOf(\"CHECK\", \"ASSUME\") + 单个 Ref 折叠",
                "evidence": "https://github.com/sqlfluff/sqlfluff/pull/8388 cubic-dev-ai P3 评论 + fix commit 'Update src/sqlfluff/dialects/dialect_clickhouse.py'",
            }
        ],
    },
    {
        "id": "T5",
        "repo": "microsoft/autogen",
        "number": 7054,
        "url": "https://github.com/microsoft/autogen/pull/7054",
        "title": "Add missing reasoning_effort parameter support for OpenAI GPT-5 models",
        "clean": False,
        "golden": [
            {
                "id": "T5-F1",
                "file": "python/packages/autogen-ext/src/autogen_ext/models/openai/config/__init__.py",
                "line": 53,
                "severity": "nit",
                "category": "style",
                "context_type": "T1",
                "description": "reasoning_effort 类型标注应符合代码库惯例（Optional[Literal[...]] 风格一致性，两处）",
                "evidence": "https://github.com/microsoft/autogen/pull/7054 BaillyM suggestions + commits 'Apply suggestion from @BaillyM'",
            },
            {
                "id": "T5-F2",
                "file": "python/packages/autogen-ext/src/autogen_ext/models/openai/config/__init__.py",
                "line": 56,
                "severity": "nit",
                "category": "style",
                "context_type": "T1",
                "description": "reasoning_effort 各级别的 docstring 描述文案改进（维护者 ekzhu 建议）",
                "evidence": "https://github.com/microsoft/autogen/pull/7054 ekzhu suggestion + applied",
            },
        ],
    },
]

data["cases"].extend(NEW)
data["notes"] = ("Wayfinder #10 golden set v2。14 条 golden findings（2 blocker / 3 concern / 9 nit）"
                 "+ 3 个干净对照，13 个 case。round 2 扩容定向补 T3/blocker：sentry#7263（T3 blocker）、"
                 "langgraph#8598（类型契约）、crewAI#7117（doc-code 一致性）等。")
p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
n_g = sum(len(c["golden"]) for c in data["cases"])
print(f"golden.json -> {len(data['cases'])} cases, {n_g} findings")
