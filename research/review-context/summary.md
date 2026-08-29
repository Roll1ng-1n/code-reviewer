# Issue #3 执行摘要：代码审查的上下文构建实践

**结论：两档方案（结构地图常驻 + import 邻域按需拉取）获得业界实践强力佐证，无需引入 RAG 全量索引。**

- **先例扫描**：主流审查工具无一采用纯 diff——CodeRabbit（沙箱全仓库克隆 + graph-based RAG + agentic 探索 + 50+ linter）、Copilot code review（agentic 全仓库分析，付费计划默认）、PR-Agent（开源：diff + hunk 扩展 + `AGENTS.md` 常驻注入 ≤500 行）、aider（tree-sitter 符号 repo map，PageRank 排序，默认 1k tokens）。Sweep 走向量检索路线（重基建，恰为本项目排除项）。diff-only 的三类失效模式（架构感知缺失、跨系统影响漏检、并发缺陷逃逸）有 CodeRabbit 官方论证文档背书。
- **import 邻域**：实现正解 = Python 用 stdlib `ast`、多语言用 tree-sitter tags（aider 37 语言现成查询文件，无纯正则先例）；注入符号签名而非全文；aider 源码证明邻域工程要点（引用改动文件的边权重 ×50、sqrt 高频阻尼、二分预算裁剪）。token 量级推算 0.5k–3k/PR，建议独立预算 1–2k（锚点：aider map 默认 1k、PR-Agent 常驻上下文 500 行 / 总预算 32k）。
- **结构地图最小形态**：目录树 + 模块级一行职责（明确不做逐文件描述，Claude Code 反模式清单）+ 构建/测试命令；≤500 行；生成走「LLM 初稿 + 人工校对 + 信号驱动修剪」（Claude Code /init + /doctor 模式）；建议采用 `AGENTS.md` 标准命名（CodeRabbit/PR-Agent/Copilot 均自动识别）。
- **提示词组织**：三层注入（全局→路径级→PR 级）为 Copilot/CodeRabbit/PR-Agent 共识；角色 = 目标声明 + 维度开关（PR-Agent require_* 式）；严重度分枚举标签派（CodeRabbit 5 级）与数值分数派（PR-Agent 0–10 + 阈值）——项目三级 blocker/concern/nit 属前者且更收敛；CodeRabbit profile（quiet/chill/assertive）直接背书「两模式共享 schema、渲染层分化」。
- **策略清单**（token 成本 × 视野，详见 findings §5）：纯 diff（无视野）→ hunk 扩展（函数级）→ **结构地图常驻（1–2k tokens，仓库级职责视野）** → 符号 repo map（1k 默认）→ **import 邻域（0.5–3k，直接依赖层）** → agentic 探索（$0.05–5/次，不可控）→ graph RAG/向量索引（重基建，已排除）。**#3+#5 组合（即两档方案）落在低成本-中高覆盖的 Pareto 前沿**，总增量约 2–5k tokens。
- **对 #9 原型票**：建议三对照组（纯 diff / diff+地图 / diff+地图+邻域）验证邻域边际增益；预期聚焦架构感知与跨系统影响两类失效（一跳邻域抓不到时序并发类缺陷）。
- **对 #8 专家团票**：每专家 = 一句话目标声明 + 维度开关 + findings JSON schema；nit 设发布阈值防噪音；fan-in 节点参考 PR-Agent self-review / CodeRabbit Verification 的复审模式。

完整调研文档：`research/review-context/findings.md`（含 21 个来源链接与取证限制说明）
