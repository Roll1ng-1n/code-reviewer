"""#22 单 Agent 基线臂 CLI 缝测试：--arm 分流 / 融合专家单次调用 / 共享 aggregate。

ScriptedProvider by_expert 按 system 提示词路由：融合专家 system 含角色标识
「四维融合审查专家」，复核过滤含「复核过滤器」——与四专家身份互不冲突，
可零网络精确脚本化基线臂的一次融合调用 + 聚合复核（与专家团共享的 FP 控制层）。

覆盖验收标准（issue #22）：
- ``--arm baseline`` → 只调一次融合专家（calls 融合调用长度 1，system 含融合
  charter 标志「四维融合审查专家」+ 四维 charter 文案）
- ``--arm panel`` 默认不变（四专家 fan-out，不出现融合专家）
- 两臂共享 aggregate：baseline 单源 findings 也走复核过滤（可观测到
  「复核过滤器」调用）；category 由模型判定（不钉死单一维度）
- ``--arm`` 非法值 → 64（argparse choices 走自定义 error）
"""

from __future__ import annotations

import json

import pytest

from reviewer import cli
from reviewer.model import ScriptedProvider

# 融合专家 system 提示词中的角色标识（与 baseline.py 常量一致）
FUSION_MARKER = "四维融合审查专家"
# 复核过滤 system 提示词中的角色标识（与 aggregate.py 常量一致）
REVIEW_MARKER = "复核过滤器"


def _findings_response(findings: list[dict]) -> str:
    """构造专家 findings JSON 响应文本（严格 JSON 契约）。"""
    return json.dumps({"findings": findings}, ensure_ascii=False)


def _baseline_provider(
    findings: list[dict], *, review_keeps: list[dict] | None = None
) -> ScriptedProvider:
    """基线臂脚本 provider：融合专家出指定 findings，复核过滤按 review_keeps 保留。

    ``review_keeps`` 缺省 = 原样保留全部（复核过滤空结果会保守保留，这里显式
    回传同批 finding 更直白，避免空结果保守语义干扰断言）。
    """
    keeps = findings if review_keeps is None else review_keeps
    return ScriptedProvider(
        by_expert={
            FUSION_MARKER: _findings_response(findings),
            REVIEW_MARKER: _findings_response(keeps),
        }
    )


def test_baseline_arm_single_fusion_call(
    run_cli, diff_file, make_finding
) -> None:
    """--arm baseline → 只调一次融合专家（system 含融合 charter 标志），无四专家调用。

    融合专家一次调用覆盖四维（architecture/logic/spec/style 融合文案），
    category 由模型按 finding 内容判定（此处脚本标 logic，保留）。
    """
    finding = make_finding(
        category="logic", file="src/calc.py", line=1,
        severity="concern", message="逻辑偏差",
    )
    provider = _baseline_provider([finding])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--arm", "baseline", "--json"
    )
    assert code == 2  # 1 concern → concerns
    fusion_calls = [c for c in provider.calls if FUSION_MARKER in c[0]]
    assert len(fusion_calls) == 1  # 融合专家只调一次
    system = fusion_calls[0][0]
    assert FUSION_MARKER in system
    # 四维 charter 融合文案标志
    assert "架构（architecture）" in system
    assert "业务逻辑（logic）" in system
    assert "规范（spec）" in system
    assert "风格（style）" in system
    # 无四专家独立调用（四专家 system 是「本次只负责「x」审查维度」）
    assert all("本次只负责" not in s for s, _ in provider.calls)
    report = json.loads(out)
    assert [f["category"] for f in report["findings"]] == ["logic"]


def test_baseline_arm_shares_aggregate_review_filter(
    run_cli, diff_file, make_finding
) -> None:
    """两臂共享 aggregate：baseline 单源 findings 也走复核过滤（可观测）。

    复核过滤脚本丢弃无把握 finding（返回空 → 但空结果保守保留全部，故改让
    复核只保留一条、丢弃另一条），断言 stderr 计数告警 + 报告只剩保留那条，
    证明基线臂同样套用 FP 控制层（与专家团相同的公平性约束）。
    """
    keep = make_finding(
        category="logic", file="keep.py", line=1,
        severity="concern", message="有把握",
    )
    drop = make_finding(
        category="style", file="drop.py", line=2,
        severity="nit", message="无把握",
    )
    provider = _baseline_provider([keep, drop], review_keeps=[keep])
    code, out, err, provider = run_cli(
        provider, "check", "--diff-file", str(diff_file), "--arm", "baseline", "--json"
    )
    assert code == 2
    # 复核过滤确实被调用（共享 aggregate 的 FP 控制层）
    review_calls = [c for c in provider.calls if REVIEW_MARKER in c[0]]
    assert len(review_calls) == 1
    report = json.loads(out)
    assert [f["message"] for f in report["findings"]] == ["有把握"]
    assert "丢弃 1 条无把握" in err


def test_panel_arm_default_unchanged(
    run_cli, diff_file, panel
) -> None:
    """--arm panel（默认）不变：四专家 fan-out（KB 空剔除 spec → 3 次），无融合专家调用。"""
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    # 无 specs/ 目录 → KB 空 → spec 被 router 剔除（#19 可留空落图），
    # architecture/logic/style 三专家各一次；全空 findings → 无复核 LLM 调用
    assert len(provider.calls) == 3
    assert all(FUSION_MARKER not in s for s, _ in provider.calls)


def test_invalid_arm_exit_64() -> None:
    """--arm 非法取值 → 64（argparse choices 走自定义 error，不与 concerns 撞码）。"""
    with pytest.raises(SystemExit) as ei:
        cli.main(["check", "--arm", "wrong"])
    assert ei.value.code == 64
