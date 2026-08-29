"""#19 Spec KB 全链路 CLI 缝测试：三层来源、hash 去重、空库剔除、注入与降级。

ScriptedProvider by_expert 零网络。覆盖验收标准：

- 三层来源（--spec > spec_kb.paths > 约定目录 specs/ 与 .reviewer/specs/）各自
  生效与同名优先级覆盖（CLI 层胜出，其余平级共存）；
- 内容 sha256 去重：同内容两路径 → documents 计 1；
- KB 空 → router 剔除 spec：calls 无 spec 身份，metadata.spec_kb 空态三键
  {loaded: false, documents: 0, hash: null}；
- KB 有 → spec 被调用且 user 含「Spec KB」块、TL;DR 与章节全文、「文档名 § 节名」
  引用格式指令；非 spec 专家不注入 KB；
- 总量 >500 行降级：TL;DR 全量在、与 diff 路径前缀命中的章节在、无关文档的
  章节不在、[spec kb truncated ...] 尾注可观测；
- config.spec_kb.paths 相对 base_dir（回放 = --repo）解析。
"""

from __future__ import annotations

import json
import re

# spec 专家 charter 标志片段（与 test_expert_panel.CHARTER_LANDMARKS["spec"] 同源）
SPEC_LANDMARK = "对照规范知识库（Spec KB）逐条检查违规"

# 通用最小规范文档（TL;DR + 一节；≤500 行 → 全量注入路径）
ORDER_DOC = """# 订单规范

> **TL;DR** 订单生命周期摘要。

## 创建订单
POST /api/orders。
"""

# 与订单文档内容区分的各层特征串（三层同名覆盖用例用）
LAYER_DOCS = {
    "convention": "# 订单规范v1\n\n> **TL;DR** 约定层版本。\n\n## 创建订单\n约定层内容。\n",
    "config": "# 订单规范v2\n\n> **TL;DR** 配置层版本。\n\n## 创建订单\n配置层内容。\n",
    "cli": "# 订单规范v3\n\n> **TL;DR** CLI 层版本。\n\n## 创建订单\nCLI层内容。\n",
}

# 改动 src/orders/create.py 的 diff（降级用例：orders.md 路径段命中）
ORDERS_DIFF = """--- a/src/orders/create.py
+++ b/src/orders/create.py
@@ -1,3 +1,4 @@
 def create():
-    pass
+    return 42
"""


def _spec_user(provider) -> str:
    """spec 专家的 user prompt（按 charter 片段过滤，零网络缝测试用）。"""
    matches = [user for system, user in provider.calls if SPEC_LANDMARK in system]
    assert len(matches) == 1, f"期望恰好一次 spec 调用，实际 {len(matches)}"
    return matches[0]


def test_three_layers_cli_overrides_same_name(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """三层各一篇同名 orders.md → 同名覆盖后只计 1，CLI 层内容胜出（优先级契约）。"""
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "orders.md").write_text(LAYER_DOCS["convention"], encoding="utf-8")
    (tmp_path / "docs-specs").mkdir()
    (tmp_path / "docs-specs" / "orders.md").write_text(LAYER_DOCS["config"], encoding="utf-8")
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra" / "orders.md").write_text(LAYER_DOCS["cli"], encoding="utf-8")
    (tmp_path / ".reviewer.yaml").write_text(
        "spec_kb:\n  paths: [docs-specs]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file),
        "--spec", str(tmp_path / "extra"), "--json",
    )
    assert code == 0
    report = json.loads(out)
    kb = report["metadata"]["spec_kb"]
    assert kb["loaded"] is True
    assert kb["documents"] == 1  # 同名覆盖链收敛为 1
    assert re.fullmatch(r"[0-9a-f]{12}", kb["hash"])  # sha256 前 12 位
    prompt = _spec_user(provider)
    assert "CLI层内容" in prompt  # --spec 最高优先
    assert "约定层内容" not in prompt
    assert "配置层内容" not in prompt


def test_convention_and_config_layers_both_load(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """约定目录 specs/ 与配置层 docs-specs/ 不同名 → 平级共存（documents 计 2）。"""
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "orders.md").write_text(ORDER_DOC, encoding="utf-8")
    (tmp_path / "docs-specs").mkdir()
    (tmp_path / "docs-specs" / "billing.md").write_text(
        "# 计费规范\n\n> **TL;DR** 计费周期。\n\n## 对账\n每月对账。\n",
        encoding="utf-8",
    )
    (tmp_path / ".reviewer.yaml").write_text(
        "spec_kb:\n  paths: [docs-specs]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    assert json.loads(out)["metadata"]["spec_kb"]["documents"] == 2
    prompt = _spec_user(provider)
    assert "订单生命周期摘要" in prompt
    assert "每月对账" in prompt


def test_reviewer_specs_convention_dir_auto_discovered(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """.reviewer/specs/ 约定目录存在即生效（不写配置、不传 --spec）。"""
    (tmp_path / ".reviewer" / "specs").mkdir(parents=True)
    (tmp_path / ".reviewer" / "specs" / "team.md").write_text(
        "# 团队规范\n\n> **TL;DR** 团队约定。\n\n## 命名\n驼峰。\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    kb = json.loads(out)["metadata"]["spec_kb"]
    assert kb["documents"] == 1
    assert kb["loaded"] is True
    assert "团队约定" in _spec_user(provider)


def test_content_dedup_same_content_counts_once(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """同内容两路径 → documents 计 1（内容 sha256 去重，不按文档名）。"""
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "general.md").write_text(ORDER_DOC, encoding="utf-8")
    (tmp_path / "specs" / "dup.md").write_text(ORDER_DOC, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    assert json.loads(out)["metadata"]["spec_kb"]["documents"] == 1
    # 渲染只保留先扫描到的一份（sorted 确定顺序），重复内容不重复注入
    prompt = _spec_user(provider)
    assert prompt.count("### 文档：") == 1
    assert "订单生命周期摘要" in prompt


def test_empty_kb_metadata_and_spec_dropped(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """KB 空（无任何来源命中）→ metadata 空态三键 + router 剔除 spec（calls 无 spec）。"""
    monkeypatch.chdir(tmp_path)  # 空快照：specs/、.reviewer/specs/ 均不存在
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    report = json.loads(out)
    assert report["metadata"]["spec_kb"] == {
        "loaded": False, "documents": 0, "hash": None,
    }
    assert len(provider.calls) == 3  # architecture / logic / style
    assert all("spec" not in system for system, _ in provider.calls)


def test_spec_called_with_kb_content_and_section_ref(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """KB 有 → spec 被调用：user 含「Spec KB」块、TL;DR 与章节全文、「文档名 § 节名」指令；
    非 spec 专家不注入 KB 块（规范维度上下文只随 spec 的 Send 携带）。"""
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "orders.md").write_text(ORDER_DOC, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    prompt = _spec_user(provider)
    assert "## Spec KB" in prompt
    assert "文档名 § 节名" in prompt  # charter 引用格式指令
    assert "订单生命周期摘要" in prompt  # TL;DR 内容
    assert "POST /api/orders" in prompt  # 章节正文（≤500 行全量注入）
    assert "### 文档：orders.md" in prompt
    # 非 spec 专家：KB 不注入（上下文切片只在 spec 的 Send 上携带）
    arch = [user for system, user in provider.calls if "architecture" in system][0]
    assert "## Spec KB" not in arch


def test_degrade_over_500_lines_keeps_tldr_and_matched_sections(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """>500 行降级：各文档 TL;DR 全量 + diff 路径前缀命中文档的章节；无关文档
    章节省略；[spec kb truncated ...] 尾注进上下文可观测。"""
    (tmp_path / "specs").mkdir()
    # orders.md：TL;DR + 与改动 src/orders/ 相关的章节 + 大量无关填充 → 行数可观
    orders_body = "\n".join(f"订单填充第 {i} 行" for i in range(400))
    (tmp_path / "specs" / "orders.md").write_text(
        "# 订单规范\n\n> **TL;DR** 订单生命周期摘要。\n\n"
        "## 创建订单\nPOST /api/orders。\n## 无关细节\n" + orders_body + "\n",
        encoding="utf-8",
    )
    # general.md：与改动路径无关的文档（TL;DR 全量保留，章节不注入）
    general_body = "\n".join(f"通用填充第 {i} 行" for i in range(200))
    (tmp_path / "specs" / "general.md").write_text(
        "# 通用规范\n\n> TL;DR 通用约定。\n\n## 命名约定\n驼峰。\n" + general_body + "\n",
        encoding="utf-8",
    )
    diff_file.write_text(ORDERS_DIFF, encoding="utf-8")  # 改动 src/orders/create.py
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    prompt = _spec_user(provider)
    # TL;DR 恒全量（两篇都在）
    assert "订单生命周期摘要" in prompt
    assert "通用约定" in prompt
    # 命中文档（orders.md）的章节在
    assert "POST /api/orders" in prompt
    # 无关文档（general.md）的章节不在——宽松启发式只收命中文档
    assert "驼峰" not in prompt
    # 降级尾注可观测
    assert "[spec kb truncated" in prompt
    # metadata 三键不受降级影响（hash 仍锚定全量内容）
    kb = json.loads(out)["metadata"]["spec_kb"]
    assert kb["loaded"] is True
    assert kb["documents"] == 2
    assert re.fullmatch(r"[0-9a-f]{12}", kb["hash"])


def test_config_paths_relative_to_base_dir(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """回放模式：config.spec_kb.paths 相对 --repo（快照仓库）解析；约定目录同以
    repo_root 为基准 → 同一目录双来源命中时去重收敛为 1（不重复计数）。"""
    repo = tmp_path / "snapshot"
    (repo / "specs").mkdir(parents=True)
    (repo / "specs" / "orders.md").write_text(ORDER_DOC, encoding="utf-8")
    (tmp_path / ".reviewer.yaml").write_text(
        "spec_kb:\n  paths: [specs]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file),
        "--repo", str(repo), "--json",
    )
    assert code == 0
    kb = json.loads(out)["metadata"]["spec_kb"]
    assert kb["loaded"] is True
    assert kb["documents"] == 1  # 配置层与约定目录指向同一 specs/ → 去重收敛
    assert "订单生命周期摘要" in _spec_user(provider)
