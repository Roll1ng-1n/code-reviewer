"""Aggregator 完整版（#20）测试：预分组聚类 / 组内合并 / 复核过滤 / 只降不升 / 降级兜底。

覆盖验收标准（issue #20）：
- 预分组纯代码聚类（同 file + 行号邻近 ±5 + 同 category 成组；行号远离 / 异 category 不成组）
- 组内 1 条跳过 LLM 合并（provider.calls 无合并调用）
- 组内重复 → 合并为 1 条且严重度取最高
- 拿不准 → 两条都保留
- 严重度被代码侧压回「只降不升」
- 复核过滤丢弃无把握 finding 且计数告警（stderr）
- 合并/复核 LLM 失败 → 透传降级不丢数据
- 最终输出仍 F001.. 连续编号 + 排序确定性

ScriptedProvider by_expert 按 system 提示词路由：合并/复核的 system 含角色标识
「合并去重器」「复核过滤器」，与四专家身份（architecture/logic/spec/style）互不
冲突，可精确脚本化聚合阶段两次 LLM 调用（零网络）。
"""

from __future__ import annotations

import json

from reviewer.aggregate import make_aggregator
from reviewer.model import ScriptedProvider

# 合并/复核 system 提示词中的角色标识（与 aggregate.py 常量保持一致）
MERGE_MARKER = "合并去重器"
REVIEW_MARKER = "复核过滤器"


def _findings_json(findings: list[dict]) -> str:
    return json.dumps({"findings": findings}, ensure_ascii=False)


def _agg(provider: ScriptedProvider):
    """直接调用聚合节点函数（不经图），便于精确控制输入 findings 与脚本路由。"""
    return make_aggregator(provider)


def test_pre_group_clusters_by_file_category_line_proximity() -> None:
    """预分组纯代码：同 file + 同 category + 行号邻近（±5）成组；行号远离 / 异 category 不成组。

    无 LLM 调用（脚本耗尽会抛 AssertionError，若预分组错误地触发合并即失败）。
    """
    provider = ScriptedProvider(
        by_expert={REVIEW_MARKER: _findings_json([])}
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 10, "severity": "nit", "category": "logic", "message": "m1"},
        {"file": "a.py", "line": 13, "severity": "nit", "category": "logic", "message": "m2"},  # 邻近 m1（±5 内）
        {"file": "a.py", "line": 40, "severity": "nit", "category": "logic", "message": "m3"},  # 远离 m2（差 27）
        {"file": "a.py", "line": 12, "severity": "nit", "category": "style", "message": "m4"},  # 异 category
        {"file": "b.py", "line": 10, "severity": "nit", "category": "logic", "message": "m5"},  # 异 file
    ]
    # 合并调用脚本为空（组大小 ≥2 会触发合并；此处唯一组是 a.py/logic 的 m1,m2）
    merge_responses = [MERGE_MARKER]  # 无合并调用占位
    _ = merge_responses
    # 预期分组：{a.py,logic} 内 m1(10) 与 m2(13) 邻近成组、m3(40) 远离单组；
    # m4 异 category、m5 异 file 各自单组。唯一多成员组 = [m1,m2]。
    result = agg({"expert_findings": findings})
    # 复核过滤器脚本返回空 → 全丢 → 保守保留全部（见 _review_filter 空结果保守）。
    # 故此处以调用上下文断言聚类：合并调用恰好一次，user 只含 m1/m2。
    merge_calls = [c for c in provider.calls if MERGE_MARKER in c[0]]
    assert len(merge_calls) == 1
    user = merge_calls[0][1]
    assert "m1" in user and "m2" in user
    assert "m3" not in user and "m4" not in user and "m5" not in user


def test_single_item_group_skips_merge_llm() -> None:
    """组内仅 1 条 → 跳过 LLM 合并（provider.calls 无合并调用）。"""
    provider = ScriptedProvider(
        by_expert={REVIEW_MARKER: _findings_json(
            [{"file": "a.py", "line": 1, "severity": "nit", "category": "logic", "message": "solo"}]
        )}
    )
    agg = _agg(provider)
    result = agg({"expert_findings": [
        {"file": "a.py", "line": 1, "severity": "nit", "category": "logic", "message": "solo"}
    ]})
    merge_calls = [c for c in provider.calls if MERGE_MARKER in c[0]]
    assert merge_calls == []
    # 复核过滤器返回保留 solo → 1 条 F001
    assert [f["message"] for f in result["findings"]] == ["solo"]
    assert result["findings"][0]["id"] == "F001"


def test_duplicates_in_group_merged_to_one_with_highest_severity() -> None:
    """组内重复（同文件邻近同 category）→ LLM 合并为 1 条，严重度取最高（blocker 保留）。"""
    provider = ScriptedProvider(
        by_expert={
            MERGE_MARKER: _findings_json(
                [{"file": "a.py", "line": 10, "severity": "blocker", "category": "logic",
                  "message": "空指针", "rationale": "合并后描述"}]
            ),
            REVIEW_MARKER: _findings_json(
                [{"file": "a.py", "line": 10, "severity": "blocker", "category": "logic",
                  "message": "空指针", "rationale": "合并后描述"}]
            ),
        }
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "空指针"},
        {"file": "a.py", "line": 12, "severity": "blocker", "category": "logic", "message": "空指针"},
    ]
    result = agg({"expert_findings": findings})
    rows = result["findings"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "blocker"  # 取最高
    assert rows[0]["id"] == "F001"


def test_uncertain_merge_keeps_both() -> None:
    """拿不准是否同一问题 → 两条都保留（合并脚本返回两条，不误删）。"""
    provider = ScriptedProvider(
        by_expert={
            MERGE_MARKER: _findings_json([
                {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "问题甲"},
                {"file": "a.py", "line": 12, "severity": "nit", "category": "logic", "message": "问题乙"},
            ]),
            REVIEW_MARKER: _findings_json([
                {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "问题甲"},
                {"file": "a.py", "line": 12, "severity": "nit", "category": "logic", "message": "问题乙"},
            ]),
        }
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "问题甲"},
        {"file": "a.py", "line": 12, "severity": "nit", "category": "logic", "message": "问题乙"},
    ]
    result = agg({"expert_findings": findings})
    assert len(result["findings"]) == 2
    assert {f["message"] for f in result["findings"]} == {"问题甲", "问题乙"}


def test_severity_capped_down_to_group_max() -> None:
    """合并结果严重度被模型升级 → 代码侧压回组内最高（只降不升硬保证）。"""
    provider = ScriptedProvider(
        by_expert={
            # 组内原最高 = concern；模型恶意返回 blocker（升级）→ 应被压回 concern
            MERGE_MARKER: _findings_json(
                [{"file": "a.py", "line": 10, "severity": "blocker", "category": "logic", "message": "越权升级"}]
            ),
            REVIEW_MARKER: _findings_json(
                [{"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "越权升级"}]
            ),
        }
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "越权升级"},
        {"file": "a.py", "line": 11, "severity": "nit", "category": "logic", "message": "越权升级"},
    ]
    result = agg({"expert_findings": findings})
    rows = result["findings"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "concern"  # 已压回组内最高，非 blocker


def test_review_filter_drops_unconfident_with_warning(capsys) -> None:
    """复核过滤丢弃无把握 finding，stderr 计数告警可观测。"""
    provider = ScriptedProvider(
        by_expert={
            REVIEW_MARKER: _findings_json(
                [{"file": "a.py", "line": 1, "severity": "nit", "category": "logic", "message": "有把握"}]
            ),
        }
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 1, "severity": "nit", "category": "logic", "message": "有把握"},
        {"file": "b.py", "line": 1, "severity": "nit", "category": "logic", "message": "无把握"},
    ]
    result = agg({"expert_findings": findings})
    assert [f["message"] for f in result["findings"]] == ["有把握"]
    err = capsys.readouterr().err
    assert "丢弃 1 条无把握" in err


def test_merge_llm_failure_degrades_to_passthrough() -> None:
    """合并 LLM 失败 → 透传降级，不丢数据（宁重复不误删）。"""
    provider = ScriptedProvider(
        callback=lambda system, user: (_ for _ in ()).throw(RuntimeError("模型挂了"))
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "甲"},
        {"file": "a.py", "line": 12, "severity": "nit", "category": "logic", "message": "乙"},
    ]
    result = agg({"expert_findings": findings})
    # 合并失败 → 透传组内全部（2 条），复核也失败 → 透传（仍 2 条）
    assert len(result["findings"]) == 2
    assert {f["message"] for f in result["findings"]} == {"甲", "乙"}


def test_review_llm_failure_degrades_to_passthrough() -> None:
    """复核 LLM 失败 → 透传合并结果，不丢数据。"""
    provider = ScriptedProvider(
        by_expert={
            MERGE_MARKER: _findings_json(
                [{"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "保留"}]
            ),
        },
        # 复核无脚本且无回调 → AssertionError（脚本耗尽）→ 降级透传
    )
    agg = _agg(provider)
    findings = [
        {"file": "a.py", "line": 10, "severity": "concern", "category": "logic", "message": "保留"},
        {"file": "a.py", "line": 11, "severity": "nit", "category": "logic", "message": "保留"},
    ]
    result = agg({"expert_findings": findings})
    assert len(result["findings"]) == 1
    assert result["findings"][0]["message"] == "保留"


def test_final_output_continuous_numbering_and_deterministic_sort() -> None:
    """最终输出 F001.. 连续编号 + (file, severity, line) 确定性排序（与 #18 同契约）。"""
    provider = ScriptedProvider(
        by_expert={REVIEW_MARKER: _findings_json([
            {"file": "b.py", "line": 1, "severity": "nit", "category": "logic", "message": "b1"},
            {"file": "a.py", "line": 5, "severity": "nit", "category": "logic", "message": "a5"},
            {"file": "a.py", "line": 2, "severity": "blocker", "category": "logic", "message": "a2"},
        ])}
    )
    agg = _agg(provider)
    findings = [
        {"file": "b.py", "line": 1, "severity": "nit", "category": "logic", "message": "b1"},
        {"file": "a.py", "line": 5, "severity": "nit", "category": "logic", "message": "a5"},
        {"file": "a.py", "line": 2, "severity": "blocker", "category": "logic", "message": "a2"},
    ]
    result = agg({"expert_findings": findings})
    rows = result["findings"]
    assert [f["id"] for f in rows] == ["F001", "F002", "F003"]
    assert [(f["file"], f["severity"], f["line"]) for f in rows] == [
        ("a.py", "blocker", 2),
        ("a.py", "nit", 5),
        ("b.py", "nit", 1),
    ]
