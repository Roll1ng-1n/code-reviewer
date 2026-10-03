"""Aggregator 完整版（实现 8/11 #20）：确定性预分组 → LLM 组内合并 → 复核过滤 → 排序编号。

这是 #11 对照实验实证裁断的「FP 控制层」落地（规格 #12 user story 21/22）：
专家团独立产出无约束会导致 FP 爆炸（v1 里 S3 产出 30 条、style 对 S6 产出 14 条），
聚合端要做三层控制：

1. **预分组（纯代码，零 LLM）**：按 ``(file, category)`` 分桶，桶内按行号邻近
   ±5 链式聚类成「合并候选组」——确定性、可复现、可调试。同文件同行号邻近又同
   category 的多条 finding 大概率描述同一底层问题，只有它们才值得进 LLM 合并；
   跨组（不同文件 / 不同 category / 行号远离）的 finding 根本不相干，绝不喂给
   LLM 误删。
2. **组内合并（LLM）**：组大小 ≥2 才调 provider，system 角色「合并去重器」+
   只降不升规则；返回数量异常或解析失败 → 保守保留原组全部（宁可重复，不可误删）。
   合并后代码侧逐条校验「严重度未高于组内原最高」，违规降回组内最高——硬保证
   「只降不升」不依赖模型自觉。
3. **复核过滤（LLM，批量）**：一次调用把所有候选 finding 传入，system「逐条判断
   是否有把握，无把握的丢弃」，返回保留列表（按候选 id 匹配回原对象，
   匹配失败保守保留）。被滤掉的 finding 在 stderr 计数告警，可观测。

收尾 ``number_findings(sort_findings(final))`` 与 #18 透传版同契约（F001.. 连续
编号 + 确定性排序）。

**关键不变式**：任一 LLM 步骤失败/异常 → 降级为「透传全部 + 排序编号」，绝不
让聚合器成为丢发现的单点（打印 warning）。合并只降不升、过滤宁留勿删——裁决权
始终在专家与人工。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .contract import Finding, SEVERITY_ORDER, number_findings, sort_findings
from .experts.base import parse_raw_findings
from .model import ModelProvider

# 行号邻近阈值：同文件同 category 内，行号差 ≤ 此值的 finding 视为同一合并候选组
_LINE_PROXIMITY = 5

# 组内合并 system prompt 角色标识（供 ScriptedProvider by_expert 路由匹配）
_MERGE_ROLE_MARKER = "合并去重器"
# 复核过滤 system prompt 角色标识（供 ScriptedProvider by_expert 路由匹配）
_REVIEW_ROLE_MARKER = "复核过滤器"


def _warning(msg: str) -> None:
    """聚合器告警统一出口（stderr，可观测）。"""
    print(f"reviewer: warning: {msg}", file=sys.stderr)


def _severity_rank(sev: str) -> int:
    """严重度权重（blocker 最高）；未知严重度排最末（保守不升级）。"""
    return SEVERITY_ORDER.get(sev, 99)


def _pre_group(findings: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """确定性预分组（纯代码，零 LLM）：(file, category) 分桶 → 行号邻近 ±5 链式聚类。

    聚类算法：桶内按 line 升序排序后，相邻 finding 行号差 ≤ ``_LINE_PROXIMITY``
    则归入同一组（链式传递：A 与 B 邻近、B 与 C 邻近 → A/B/C 同组），否则开新组。
    每组为「合并候选组」，返回按 (file, category) 稳定序排列的组列表（组序不影响
    最终输出——收尾会全局重排编号，但保证确定性）。

    单条 finding 自成一组（组大小 1）：不触发 LLM 合并（见 _merge_group），
    直接作为候选流入复核过滤。
    """
    # (file, category) 分桶（保序去重 key，避免排序抖动）
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in findings:
        key = (f.get("file", ""), f.get("category", ""))
        buckets.setdefault(key, []).append(f)

    groups: list[list[dict[str, Any]]] = []
    for (file, category) in sorted(buckets):
        bucket = sorted(buckets[(file, category)], key=lambda f: f.get("line", 0))
        current: list[dict[str, Any]] = []
        prev_line: int | None = None
        for f in bucket:
            line = f.get("line", 0)
            if prev_line is None or (line - prev_line) <= _LINE_PROXIMITY:
                current.append(f)
            else:
                groups.append(current)
                current = [f]
            prev_line = line
        if current:
            groups.append(current)
    return groups


def _merge_group(
    provider: ModelProvider, group: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """组内合并（LLM）：组大小 ≥2 才调用；失败/异常 → 保守返回原组全部。

    返回的 finding 逐一校验「严重度未高于组内原最高」，违规降回组内最高——
    代码侧硬保证「只降不升」。返回数量 > 原组大小（模型不该新增）→ 视为异常，
    保守保留原组全部。
    """
    if len(group) < 2:
        return group

    original_max_rank = min(_severity_rank(f.get("severity", "")) for f in group)
    original_max_sev = min(
        (f.get("severity", "") for f in group),
        key=lambda s: _severity_rank(s),
    )

    system = (
        f"你是{_MERGE_ROLE_MARKER}。多个审查专家审查了同一个 diff，以下 findings "
        "可能描述同一底层问题。判断哪些描述同一问题并合并：\n"
        "- 合并时保留最完整的描述，严重度取其中最高（blocker>concern>nit）\n"
        "- 拿不准是否同一问题时，两条都保留\n"
        "- 不新增、不删除任何非重复 findings，不改写内容\n"
        "- 严重度只能取组内已有的值，绝不允许升级\n"
        "## 输出契约（严格 JSON，不要输出任何其它内容）\n"
        '{"findings": [...]}'
    )
    user = f"【findings】{json.dumps(group, ensure_ascii=False)}"

    try:
        raw = provider.complete(system=system, user=user)
        parsed = parse_raw_findings(raw)
    except Exception as exc:  # noqa: BLE001 —— 聚合器绝不当丢发现的单点
        _warning(f"合并 LLM 调用失败，保守保留原组全部 {len(group)} 条：{exc}")
        return group

    # 数量异常（模型新增 findings）→ 保守保留原组全部
    if len(parsed) > len(group):
        _warning(
            f"合并返回 {len(parsed)} 条超过组内 {len(group)} 条，保守保留原组全部"
        )
        return group

    merged: list[dict[str, Any]] = []
    for item in parsed:
        try:
            # category 用组内原值钉死（不信任模型自我标注）
            finding = Finding.model_validate({**item, "category": group[0]["category"]})
        except ValidationError:
            # 单条解析失败 → 保留原组全部（该条无法可靠还原，宁可重复）
            _warning("合并结果单条未通过校验，保守保留原组全部")
            return group
        merged.append(finding.model_dump(exclude_none=True))

    # 代码侧「只降不升」硬保证：严重度高于组内原最高 → 降回组内最高
    capped: list[dict[str, Any]] = []
    for f in merged:
        if _severity_rank(f["severity"]) < original_max_rank:  # 权重更小 = 更严重 = 升级
            _warning(
                f"合并结果严重度 {f['severity']} 高于组内最高 {original_max_sev}，"
                "降回组内最高（只降不升）"
            )
            f = {**f, "severity": original_max_sev}
        capped.append(f)

    if not capped:
        # 合并结果为空（模型删光了）→ 保守保留原组全部
        _warning("合并结果为空，保守保留原组全部")
        return group
    return capped


def _review_filter(
    provider: ModelProvider, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """复核过滤（LLM，批量）：一次调用逐条判断「是否有把握」，无把握的丢弃。

    返回的保留列表按独立候选 id 匹配回原对象。响应必须完整合法，未知、重复
    id 或改写字段均导致整批保守保留。被滤掉的 finding 计数告警。
    任一异常 → 保守保留全部。
    """
    if not candidates:
        return candidates

    system = (
        f"你是{_REVIEW_ROLE_MARKER}。以下 findings 即将进入最终报告。逐条判断："
        "这条 finding 是否有充分把握（描述清楚、有理有据、确为真实问题）。\n"
        "- 有把握的保留；无把握的丢弃\n"
        "- 不新增、不修改任何 finding 的内容，只做保留/丢弃二选一\n"
        "- 每个候选的 id 是独立身份，只返回要保留的 id；不得重复、编造 id\n"
        "## 输出契约（严格 JSON，不要输出任何其它内容）\n"
        '{"findings": [{"id": "C001"}]}'
    )
    indexed = {f"C{i:03d}": f for i, f in enumerate(candidates, start=1)}
    payload = [{**f, "id": cid} for cid, f in indexed.items()]
    user = f"【findings】{json.dumps(payload, ensure_ascii=False)}"

    try:
        raw = provider.complete(system=system, user=user)
        kept_raw = parse_raw_findings(raw)
    except Exception as exc:  # noqa: BLE001 —— 聚合器绝不当丢发现的单点
        _warning(f"复核过滤 LLM 调用失败，保守保留全部 {len(candidates)} 条：{exc}")
        return candidates

    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in kept_raw:
        cid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(cid, str) or cid not in indexed or cid in seen:
            _warning("复核响应包含缺失、未知或重复候选 id，保守保留全部")
            return candidates
        original = indexed[cid]
        if any(key != "id" and (key not in original or value != original[key])
               for key, value in item.items()):
            _warning("复核响应改写候选内容，保守保留全部")
            return candidates
        seen.add(cid)
        kept.append(original)

    if not kept:
        _warning("复核过滤结果为空的异常情况，保守保留全部")
        return candidates
    dropped = len(candidates) - len(kept)
    if dropped > 0:
        _warning(f"复核过滤丢弃 {dropped} 条无把握 finding（{len(candidates)} → {len(kept)}）")
    return kept


def make_aggregator(provider: ModelProvider) -> Callable[[dict], dict]:
    """聚合节点工厂：闭包捕获 provider——测试传假 provider 即得零网络缝。

    与专家节点同模式（make_*_expert），graph.py 在 build_review_graph 里
    闭包注入 provider。唯一写 ``findings`` 的节点（整体替换安全，见 state.py）。
    """

    def aggregate(state: dict) -> dict:
        """fan-in 聚合节点：预分组 → 组内合并 → 复核过滤 → 排序编号。

        关键不变式：任一 LLM 步骤失败都降级为「透传全部 + 排序编号」，
        绝不让聚合器成为丢发现的单点。
        """
        expert_findings: list[dict[str, Any]] = state.get("expert_findings", [])

        # 无 findings → 直接空输出（不触发任何 LLM 调用）
        if not expert_findings:
            return {"findings": []}

        # 1. 预分组（纯代码）
        groups = _pre_group(expert_findings)

        # 2. 组内合并（LLM，仅组大小 ≥2）
        try:
            merged_flat: list[dict[str, Any]] = []
            for group in groups:
                merged_flat.extend(_merge_group(provider, group))
        except Exception as exc:  # noqa: BLE001 —— 兜底：透传全部，绝不丢发现
            _warning(f"聚合合并阶段异常，降级透传全部 {len(expert_findings)} 条：{exc}")
            return {"findings": number_findings(sort_findings(expert_findings))}

        # 3. 复核过滤（LLM，批量）
        try:
            final = _review_filter(provider, merged_flat)
        except Exception as exc:  # noqa: BLE001 —— 兜底：透传合并结果，绝不丢发现
            _warning(f"聚合复核阶段异常，降级透传合并结果 {len(merged_flat)} 条：{exc}")
            final = merged_flat

        # 4. 收尾：确定性排序 + 统一编号（与 #18 透传版同契约）
        return {"findings": number_findings(sort_findings(final))}

    return aggregate
