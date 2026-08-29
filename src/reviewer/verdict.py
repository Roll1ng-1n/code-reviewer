"""Verdict 节点：程序化派生 summary（零 LLM 调用）。"""

from __future__ import annotations

from .contract import derive_verdict


def verdict_node(state: dict) -> dict:
    return {"summary": derive_verdict(state.get("findings", []))}
