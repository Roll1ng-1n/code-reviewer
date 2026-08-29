# 执行摘要：LLM 代码审查的评估方法论（Issue #4）

**结论：初定的「golden set 为主（10-20 真实 PR + 人工标注 + 干净 PR 对照）」方向正确，与三个最新基准（Martian 50 PR、SWR-Bench 500/500 Change/Clean 平衡、SWE-PRBench 350 PR）实践吻合；核心建议是匹配协议放弃行级精确匹配、采用「文件硬约束 + LLM judge 语义等价 + 行号 ±3 行分层信号」，工具上主推 LangSmith + 自写 Python evaluator（LangGraph 原生契合）。**

1. **规模**：10-20 偏小（Anthropic 建议 20-50 起步），需按「问题类型 × 上下文层级」分层并附 Wilson 置信区间；标注成本约数人时（SWR-Bench 5 人标 1000 PR 为参照）。
2. **指标**：P/R/F1 基于二部图一对一语义匹配（TP 判定由 judge 完成）；另报告干净 PR 假阳性率、幻觉率、严重度混淆矩阵 + 高严重度漏报率（单独门禁）；重复 ≥3 次报均值±std 与 pass@k/pass^k。业界（Martian、SWR-Bench）明确不用行级匹配——LLM 行号映射天然漂移。
3. **LLM-as-judge 可靠**：本场景是「结构化、有参照、判定面窄」的任务，SWR-Bench judge 与人工原始一致 ~90%（κ 52.8-62.0），SWE-PRBench κ=0.75；远好于开放式打分（G-Eval Spearman 0.514）。必须缓解：自我增强（judge 与被评模型异族）、冗长偏差（校准集放对照样本）；单 finding 判定天然免疫位置偏差。
4. **人工抽检**：初建 golden set 100% 人工；常规 LLM 全量 + 定向抽检 15-25%（borderline/UNKNOWN 全查），一致性用 Cohen's kappa 报告。
5. **工具**：匹配内核（容差 + 混淆矩阵）三家工具都不内置、必须自写 → 选型看生态：主推 LangSmith（LangGraph 原生 trace、trace→dataset 闭环、实验对比直接支撑 #11 对照实验）；免 SaaS 依赖则 promptfoo（javascript 断言 + llm-rubric）；DeepEval 优先级最低。
6. **对照实验（#11）文献支撑**：SWR-Bench 实证多评审聚合 n=10 时 F1 +43%、召回翻倍，「专家团 vs 单 Agent」假设成立；注意绝对分数不重要（前沿模型最高 F1 仅 19.38%），配对差异才重要。

详细文档：research/eval-methodology/findings.md（含指标公式、judge 提示词草案、来源 URL）。
