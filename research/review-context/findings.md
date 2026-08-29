# 调研：代码审查的上下文构建实践

- **Issue**: [#3 调研：代码审查的上下文构建实践](https://github.com/Roll1ng-1n/code-reviewer/issues/3)
- **调研日期**: 2026-08-29
- **调研人**: researcher-r2（wayfinder-research 团队）
- **调研问题**: 「架构级视野」两档方案（结构地图常驻 + import 邻域按需拉取）的业界实践佐证
- **服务对象**: 专家团 roster 票（#8）、业务逻辑一致性风险验证原型票（#9）

---

## 0. 结论速览

1. **没有一个主流 AI 审查工具只用纯 diff。** CodeRabbit 官方论证文档直接把「diff-only」列为三类失效模式（架构感知缺失、跨系统影响漏检、并发缺陷逃逸）的根源；GitHub Copilot code review 已把「agentic 全仓库分析」设为所有付费计划默认；开源的 PR-Agent 默认注入 `AGENTS.md` 常驻上下文。业界下限是「diff + 某种仓库视野」，不是纯 diff。
2. **两档方案不是拼凑，是两条各有先例的成熟路线的组合**：结构地图常驻 ≈ PR-Agent / Copilot / CodeRabbit 的「AGENTS.md / CLAUDE.md 注入」模式；import 邻域按需 ≈ aider 的「tree-sitter 引用图 + PageRank 排序、按 token 预算裁剪」模式。aider 源码里甚至有专门服务于「邻域」的工程细节（引用了当前编辑文件的边权重 ×50）。
3. **RAG 全量索引被业界重工具采用（CodeRabbit graph-based RAG、Sweep 向量检索、JetBrains 语义索引），但轻量级工具证明了不上索引也能获得架构视野**——这是本项目「排除 RAG、采用两档」决策的正面佐证。
4. **token 成本锚点齐全**：aider repo map 默认 1k tokens；PR-Agent 仓库上下文上限 500 行、总预算 `max_model_tokens=32000`；Copilot 指令文件建议 ≤1000 行；Copilot 审查每次 $0.05–5。import 邻域按符号签名拉取，估算 0.5k–3k tokens/PR（见 §2.4，推算已标注）。
5. **提示词组织有清晰范式**：三层指令注入（全局 → 路径级 → PR 级）是 Copilot / CodeRabbit / PR-Agent 三家共识；严重度体系分两派（枚举标签派 vs 数值分数派）；「同一发现管道、不同呈现参数」的 profile 机制（CodeRabbit quiet/chill/assertive）直接支撑本项目「两模式共享 schema、渲染层分化」的既定决策。

---

## 1. 先例扫描：审查工具的上下文构成

### 1.1 总表：纯 diff 还是 diff + 检索？

| 工具 | 上下文构成 | 纯 diff？ | 结构性视野来源 |
|---|---|---|---|
| **aider**（编码助手，repo map 先例） | 聊天文件 + **tree-sitter 符号级 repo map**（PageRank 排序，1k token 预算） | 否 | tree-sitter tags（defs/refs）→ 引用图 |
| **CodeRabbit** | diff + **沙箱全仓库克隆** + agentic 自主探索 + graph-based RAG（AST/数据流/依赖图）+ 50+ 静态分析器 + learnings + code guidelines | 否（重检索） | AST/依赖图/调用图的显式图表示 |
| **GitHub Copilot code review** | PR 变更 + **agentic 全仓库分析**（付费计划默认启用）+ 指令文件 + MCP 服务器 + Memory | 否（agentic 检索） | agent 自主在仓库内探索 |
| **Sweep**（现 JetBrains 助手，原 issue→PR agent） | issue 理解 → **向量检索相关代码**（OpenAI/Voyage embedding）→ 规划变更 | 否（向量检索） | 向量嵌入索引 |
| **PR-Agent / Qodo**（开源，可复刻） | PR diff + **hunk 行扩展**（+5/-1 行）+ 动态扩展到外层函数 + **`AGENTS.md` 常驻注入**（≤500 行） | 否（轻量结构上下文） | 人工维护的 AGENTS.md |

来源：见 §1.2–§1.6 各小节。

### 1.2 aider：tree-sitter repo map（「结构地图 + 邻域」最完整的技术先例）

**机制**（官方文档 + 博客 + 源码三方确认）：

- repo map = 文件列表 + 每文件关键符号（类/方法/函数）的**签名级代码行**，随每次请求发送。
- **tree-sitter 解析**：用各语言的 `tags.scm` 查询文件提取 `name.definition.*`（defs）与 `name.reference.*`（refs）两类标签；语言包为 `tree-sitter-language-pack`（新）/ `py-tree-sitter-languages`（旧），pip 直装。
- **图排序**：文件为节点、标识符引用为边构建 `networkx.MultiDiGraph`，跑 **PageRank**（源码：`nx.pagerank(G, weight="weight", personalization=...)`）。被引用最多的标识符最重要。
- **token 预算**：`--map-tokens` 默认 **1k tokens**；用**二分搜索**裁剪地图直到落进预算（15% 容差）；聊天中没有文件时预算放大 **8 倍**（`map_mul_no_files=8`，受上下文窗口 −4096 padding 限制），因为需要「看全仓库」。
- **解析失败兜底**：tags 查询只有 defs 没有 refs 时（如 C++），用 **pygments 词法分析回填 refs**——tree-sitter 不是唯一手段，是「AST 优先、词法兜底」的双层实现。

**与「import 邻域」直接相关的工程细节**（`repomap.py` 源码）：

- **个人化权重（personalization）**：聊天中的文件权重 `100/num_files`；被提及的文件/标识符 ×10。
- **邻域偏置**：`use_mul *= 50` —— 引用边里，**引用端是当前聊天文件时，边权重放大 50 倍**。这就是「以改动文件为中心的依赖邻域」的排序实现：不是全仓库均匀撒，而是把「谁依赖我、我依赖谁」顶到排名前面。
- **高频标识符阻尼**：`num_refs = math.sqrt(num_refs)`——被引用 100 次的常见词只算 10，防止高频低价值符号淹没地图。
- **命名启发式**：snake/kebab/camel 且 ≥8 字符的标识符 ×10（更像有业务含义）；下划线开头 ×0.1；>5 个文件都定义的重名符号 ×0.1。
- **缓存**：tags 结果存 SQLite（diskcache），按文件 mtime 失效。

来源：
- 文档：https://aider.chat/docs/repomap.html
- 博客《Building a better repository map with tree sitter》（2023-10-22）：https://aider.chat/2023/10/22/repomap.html
- 源码（本次抓取 main 分支）：https://github.com/Aider-AI/aider/blob/main/aider/repomap.py
- 语言支持（37 种语言有 repo map，主流全覆盖）：https://aider.chat/docs/languages.html

### 1.3 CodeRabbit：重上下文路线的代表（也是「为什么需要架构视野」的论证库）

**上下文构成**（官方架构页）：

1. 沙箱化云端**完整克隆仓库**（非只看变更文件）；
2. **50+ 静态分析器 / linter / SAST** 并行；
3. **Agentic 探索**：agent 自主在代码库中检索、调查；
4. 专用 agent 并行（Review / Verification / Chat / Pre-Merge Checks）；
5. **活记忆（learnings）**：从用户驳回的评论、历史 PR、issue、编码规范中持续学习；
6. 企业集成（issue tracker、MCP、多仓库）。

**CodeRabbit 官方《Code Context》论证文档**（2026-06-24，本调研最重要的论证来源）给出的分层模型：

| 层 | 内容 | 机制 |
|---|---|---|
| 0 | Diff（基线） | 唯一不需要额外获取的层 |
| 1 | Codebase awareness | **graph-based RAG**：基于 AST、数据流图、依赖图构建显式图表示，边编码函数调用、继承、import、数据与控制流 |
| 2 | Conventions（团队规范） | 版本控制的配置（Code Guidelines、Path Instructions） |
| 3 | Learnings | 被驳回的反馈保持被驳回状态 |
| 4 | Docs / 外部工件 | 关联工单、既往 PR、决策记录，由 context engine 持久索引 |

**diff-only 的三类失效模式**（该文引 Cloudflare 工程团队）：

1. **架构感知缺失**：审查者不了解系统为何如此设计；
2. **跨系统影响漏检**：「一个 API 契约变更可能破坏三个下游消费者」；
3. **并发缺陷逃逸**：时序依赖 bug 很难从静态 diff 捕获。

**为什么不能只堆大上下文窗口**：Lost in the Middle（相关信息位于长上下文中部时性能显著退化）+ 因果注意力偏置 + 上下文窗口没有内建的代码结构概念 → **必须有检索/结构层，而非塞满 token**。

**审查质量数据（自报）**：Common App 审查时间 −35%、抓到 SonarQube 漏检的竞态条件；freee 六个月节省 32.8 周审查时间。审查发现带**严重级别**（🔴Critical/🟠Major/🟡Minor/🔵Trivial/⚪Info）与**类别徽章**（安全/稳定性/数据完整性/功能正确性/性能/可维护性共 6 类）。

来源：
- 架构页：https://docs.coderabbit.ai/overview/architecture
- Code Context 论证文档：https://www.coderabbit.ai/guides/code-context
- 审查流程概览：https://docs.coderabbit.ai/guides/code-review-overview
- 配置参考（2026-08-24 更新）：https://docs.coderabbit.ai/reference/configuration

### 1.4 GitHub Copilot code review：diff 输入 + 默认 agentic 全仓库分析

**输入构成**（官方文档）：

- 审查对象 = PR 中的变更；排除依赖管理文件（`package.json`、lock 文件）、日志、SVG。
- **Agentic 能力（所有付费计划默认启用）**：分析**整个仓库**来理解变更上下文——输入不仅是 diff。依赖 GitHub Actions 执行（Actions 不可用时退化为「更有限的审查」= 接近 diff-only）。
- 上下文增强源：自定义指令（head 分支读取）、agent skills、MCP 服务器（GitHub MCP server 与 Playwright MCP server 默认启用）、Copilot Memory（预览）。
- **审查力度分级**：Lite（默认，$0.05–1/次）vs Balanced（更高推理模型，复杂逻辑/安全敏感/跨服务变更，$0.25–5/次）。

**指令注入三层结构**：

| 层 | 文件 | 机制 |
|---|---|---|
| 仓库级 | `.github/copilot-instructions.md` | 全局生效，自动读取 |
| 路径级 | `.github/instructions/**/*.instructions.md` | `applyTo` glob frontmatter 圈定作用域 |
| Agent 级 | `AGENTS.md`（目录树就近优先）/ 根目录 `CLAUDE.md` | 跨工具共享的代理指令 |

**编写规范**（官方教程）：单文件 ≤**1000 行**（超长导致指令被忽略）；简短祈使句 + 要点符号；正反例代码；从 10–20 条最小集起步、用真实 PR 迭代。**不支持**的定制：改变评论格式、阻断 PR 合并、遵循外部链接、模糊质量要求（「更准确一点」类纯噪音）。

来源：
- About code review：https://docs.github.com/en/copilot/concepts/agents/code-review
- Customize code review 教程：https://docs.github.com/en/copilot/tutorials/customize-code-review
- 自定义指令配置：https://docs.github.com/copilot/how-tos/use-copilot-agents/request-a-code-review/configure-coding-guidelines

### 1.5 Sweep：向量检索路线（取证受限，如实标注）

- **现状**：仓库已转型为「JetBrains IDE 的 AI 编码助手」（autocomplete + agent），原 GitHub App（issue→PR）形态成为历史。7.7k stars。
- **检索机制**（DeepWiki 基于源码自动生成，索引于 2026-05-20）：Sweep 使用**向量嵌入**搜索代码库，embedding 由 **OpenAI 与 Voyage AI** 生成，核心实现在 `sweepai/core/vector_db.py`；issue 处理流程 = 理解 issue → **搜索相关代码** → 规划变更（FileChangeRequest 实体）→ 创建 PR。仓库 topics 含 `code-search`。
- **取证限制**：Sweep 官方博客（blog.sweep.dev）当前返回 402 Payment Required，Wayback 快照抓取超时，其上下文引擎的博客级细节本次**未能核实**，不做转述。以下判断仅基于源码级证据：Sweep 走的是**向量语义检索**路线（与 CodeRabbit 的 graph RAG、aider 的符号图并列的第三条路线），对本项目的参考价值主要在于「向量索引是重基建选项，本项目已明确排除」。

来源：
- https://github.com/sweepai/sweep
- https://deepwiki.com/sweepai/sweep
- 博客（不可达，仅存目）：https://blog.sweep.dev/posts/autocomplete-context

### 1.6 PR-Agent / Qodo：开源、可逐行复刻的「轻量结构上下文」范本

PR-Agent（11k+ stars，Qodo 开源）的 `/review` 与 `/improve` 上下文构成（`configuration.toml` 原文，本次抓取 main 分支）：

**diff 邻域扩展**（比 import 邻域更轻的方案，可作对照）：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `patch_extra_lines_before` | 5 | 每个 hunk 前额外行数 |
| `patch_extra_lines_after` | 1 | 每个 hunk 后额外行数 |
| `allow_dynamic_context` | true | 动态上下文 |
| `max_extra_lines_before_dynamic_context` | 10 | hunk 向前扩展**直到外层函数/类定义**的行数上限 |
| `large_patch_policy` | "clip" | 大补丁截断或跳过 |

**常驻仓库上下文**：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `repo_context_files` | `["AGENTS.md"]` | 注入的常驻上下文文件（可加 CLAUDE.md 等） |
| `repo_context_max_lines` | **500** | 常驻上下文行数上限 |
| `repo_context_from_default_branch` | true | 从默认分支读取 |

PyPI 发布说明证实：「AGENTS.md and friends are now fed to /review, /describe and /improve **by default**」。

**总 token 预算**：`max_model_tokens=32000`（硬上限）、`max_description_tokens=500`、`max_commits_tokens=500`、`model_token_count_estimate_factor=0.3`（估算放大系数防超限）。

来源：
- https://github.com/qodo-ai/pr-agent/blob/main/pr_agent/settings/configuration.toml（raw 抓取于 2026-08-29）
- https://pypi.org/project/pr-agent/
- 配置参考（DeepWiki）：https://deepwiki.com/qodo-ai/pr-agent/7.1-configuration-reference

---

## 2. import 邻域：实现方式与 token 成本

### 2.1 提取方式对比：正则 / AST / tree-sitter

| 方式 | 原理 | 优缺点 | 先例 |
|---|---|---|---|
| **正则** | 匹配 `import X` / `from X import Y` 文本模式 | 零依赖、最快；但边界 case 多（相对导入 `from . import x`、多行括号导入、别名 `import numpy as np`、字符串内假阳性、动态 `__import__`）。**未发现任何主流工具纯用正则做依赖提取** | 无（仅作为反面参照） |
| **语言原生 AST**（Python stdlib `ast`） | 解析 `Import` / `ImportFrom` 节点 | Python 项目零依赖、100% 语法正确；单语言 | Python 生态标准做法 |
| **tree-sitter** | 每语言一份 `tags.scm` 查询，捕获 defs/refs | 多语言统一管线（aider 37 种语言现成查询文件）、增量解析、容错性强；需要引入依赖 | **aider**（tags 查询）、**ast-grep**（CodeRabbit 用于 AST 级路径指令） |

**结论**：Python MVP 用 stdlib `ast`（零依赖且正确）；多语言扩展走 tree-sitter tags（直接复用 aider 的查询文件，MIT/Apache 许可，见其博客附注）。aider 的「AST 优先 + pygments 词法兜底」双层策略值得抄：解析失败的文件不应让整次审查崩溃。

### 2.2 aider 引用图里可直接借鉴的工程决策

1. **邻域 = 双向**：「谁引用了我」（上游影响面）与「我引用了谁」（下游依赖）都进图；用边权重而非硬过滤表达（`use_mul *= 50`），保证邻域外的高价值符号仍有机会入选。
2. **排序而非选择**：邻域不是二元的「进/不进」，而是 PageRank 排序 + token 预算截断——预算不够时自然留下最重要的。
3. **高频阻尼**：`sqrt(num_refs)` 防止 `logging`、`os` 这类被引用上千次的符号挤爆预算。
4. **签名渲染**：用 grep_ast 的 `TreeContext` 只渲染「感兴趣的行」（lines-of-interest）及其语法父级上下文——即**拉签名行，不拉全文**。
5. **缓存**：tags 结果按文件 mtime 缓存（SQLite），仓库扫描只慢第一次。

### 2.3 拉什么进上下文：签名而非文件全文

业界一致做法是把依赖以**符号签名**（class/def 定义行 + 参数类型）形式注入，而非整个文件：

- aider repo map 的输出形态就是「文件名 + 关键符号签名行」；
- PR-Agent 的动态上下文扩展到「外层函数/类」为止（`max_extra_lines_before_dynamic_context=10`），同样是结构边界而非全文；
- CodeRabbit 的 graph-based RAG 在 AST/依赖图上检索，注入的是图上的证据切片。

### 2.4 token 成本量级（锚点 + 推算）

**有出处的锚点**：

| 锚点 | 数值 | 来源 |
|---|---|---|
| aider repo map 默认预算 | **1k tokens**（全仓库视角） | aider 文档 |
| aider 无聊天文件时预算 | 8k tokens（1k × `map_mul_no_files=8`，受窗口限制） | aider 源码 |
| PR-Agent 常驻仓库上下文 | **500 行**（≈1–2k tokens） | PR-Agent configuration.toml |
| PR-Agent 单次审查总预算 | `max_model_tokens=32000` | 同上 |
| Copilot 指令文件建议上限 | 1000 行 | Copilot 官方教程 |
| Copilot 审查单价 | Lite $0.05–1 / Balanced $0.25–5 每次 | Copilot 官方文档 |

**推算（无直接出处，标注为估算）**：一个典型 Python 业务文件的直接 import 涉及 10–60 个本仓库符号；每个 class/def 签名约 20–50 tokens。按一次审查 3–5 个改动文件、每文件取直接依赖中最相关的符号签名计算，**import 邻域的量级约 0.5k–3k tokens/PR**，与 aider「1k tokens 管全仓库地图」的预算观一致。**建议给邻域设独立预算 1–2k tokens 起步**，超限时按引用权重截断（aider 式），不整文件拉取。

---

## 3. 结构地图最小形态：目录树 + 模块职责说明

### 3.1 业界三种生成模式并存

| 模式 | 先例 | 说明 |
|---|---|---|
| **纯人工** | AGENTS.md 标准（60k+ 开源项目采用，LF 旗下 Agentic AI Foundation 托管） | 标准 FAQ 明确：就是普通 Markdown，无 schema，人写人维护 |
| **LLM 生成 + 人工校对**（主流） | Claude Code `/init` 自动生成 CLAUDE.md「起点」→ 人工精炼；`/doctor` 自动提出删减建议（反向维护）；Copilot cloud agent 可自动生成 `copilot-instructions.md` 草稿 PR 请人审查 | 生成是廉价的，**校对与持续修剪才是工程重点**；Anthropic 把 CLAUDE.md 类比为「需要持续运维的代码」 |
| **全自动**（符号级） | aider tree-sitter repo map；JetBrains Context 增量语义索引 | 无需人工，但输出是**符号/API 面**，不含人类语义的「职责说明」 |

**关键发现**：「职责说明」这类人类语义信息，业界全部走**人工或 LLM+人工**通道；全自动方案（aider/JetBrains）只能产出符号结构，产不出「这个模块负责什么」。两者是互补而非替代——这正是两档方案「结构地图（人工语义）+ import 邻域（自动符号）」的分工逻辑。

### 3.2 内容规范：什么进、什么不进

**Claude Code 官方清单**（对「最小形态」最直接的回答）：

| 应收录 | 不应收录 |
|---|---|
| 猜不到的 Bash 命令 | 读代码就能弄清的内容 |
| 偏离默认的代码风格规则 | 标准语言惯例 |
| 测试指令 | 详细 API 文档（放链接） |
| 仓库礼仪（分支/PR 规范） | 频繁变动的信息 |
| 项目特有架构决策 | **逐文件的代码库描述** |
| 环境怪癖、常见陷阱 | 「写干净代码」类自明要求 |

准入判断标准：「删掉这一行会导致 agent 犯错吗？」——不会就删。**膨胀的地图反而导致指令被忽略**（Anthropic 与 Copilot 文档均有同款警告：过长指令文件部分被忽略）。

→ 对本项目的转译：**目录树 + 每模块一行职责**（不到文件级）+ 构建/测试命令 + 项目特有约定，就是「最小形态」的业界对齐版本。Claude Code 明确反对逐文件描述，职责粒度应止于目录/模块级。

**AGENTS.md 标准的内容建议**：项目概览、构建/测试命令、代码风格、测试指令、安全注意事项、PR/提交规范——「任何你会告诉新同事的事」。嵌套规则：monorepo 子包可放独立 AGENTS.md，agent 读取**离被编辑文件最近**的那份（就近优先）。

### 3.3 规模锚点

- PR-Agent：`repo_context_max_lines=500`（含包装标签）——可直接借用为本项目结构地图的行数上限。
- Copilot：单指令文件 ≤1000 行的指引（超过则质量下降）。
- Claude Code：常驻内容必须「广谱适用」，偶尔才需要的知识放进按需加载的 Skills——**「常驻地图精简 + 详细知识按需」的分层正是两档方案的思想内核**。

### 3.4 与 Spec KB 的关系（术语澄清）

CodeRabbit 把 `CLAUDE.md` / `AGENTS.md` / `.cursorrules` 等统一作为 code guidelines（审查标准）自动拾取，即「描述性地图」与「规范性标准」在同一通道注入。本项目 CONTEXT.md 已区分两者（结构地图管「仓库是什么」，Spec KB 管「应该怎样」且可空、空则跳过检查）。**建议**：保持术语与通道分离，但实现上可让 Spec KB 条目走与结构地图相同的注入基建（不同的注入槽位）；文件命名采用 `AGENTS.md` 生态标准以获得未来跨工具复用性（CodeRabbit / PR-Agent / Copilot 均自动识别）。

来源：
- Claude Code 最佳实践：https://code.claude.com/docs/en/best-practices
- AGENTS.md 标准：https://agents.md/
- CodeRabbit code guidelines（自动检测清单、目录作用域、跨仓库引用）：https://docs.coderabbit.ai/knowledge-base/code-guidelines
- JetBrains Context（2026-07，增量语义索引 + agent 工具，自报最高 −48% 成本）：https://blog.jetbrains.com/ai/2026/07/introducing-jetbrains-context-repository-intelligence-for-coding-agents/

---

## 4. 提示词组织：角色、严重度、输出格式

### 4.1 角色与指令分层

三家共识的**三层注入结构**：

| 层 | Copilot | CodeRabbit | PR-Agent |
|---|---|---|---|
| 全局 | `copilot-instructions.md` | 全局 tone / profile | `[config] response_language` 等全局节 |
| 路径级 | `*.instructions.md`（applyTo glob） | `path_instructions`（glob，≤20k 字符） | — |
| PR/工具级 | PR 描述中的标识符引导 MCP | 摘要指令、reviewer instructions | `extra_instructions`（每工具一节） |

**角色定义的两种写法**：

- **目标声明式**（Copilot AGENTS.md 示例）："Your primary goal is to validate that incoming code changes are secure, performant, and match this repository's engineering standards." —— 一句话角色 + 反模式清单。
- **维度开关式**（PR-Agent `[pr_reviewer]`）：`require_security_review=true`、`require_tests_review=true`、`require_estimate_effort_to_review=true`、`require_ticket_analysis_review=true`、`require_score_review=false`——用布尔开关组合出审查维度，每个开关对应提示词的一个 section。
- CodeRabbit 的 profile（`quiet`/`chill`/`assertive`）控制反馈量（甚至联动 PHPStan 严格度：chill=level 3，assertive=level 8），`tone` 是 ≤250 字符的自由提示词注入。

### 4.2 严重度体系对比

| 工具 | 体系 | 形态 |
|---|---|---|
| **CodeRabbit** | 🔴Critical / 🟠Major / 🟡Minor / 🔵Trivial / ⚪Info（5 级）+ 6 类内容徽章 | 枚举标签 |
| **PR-Agent** `/improve` | 0–10 分 + 阈值（`th_high=9`、`th_medium=7`，`suggestions_score_threshold` 可过滤低分建议） | 数值分数 |
| **Copilot** | 无用户可配置严重度（内部按 effort level 路由模型） | 隐式 |
| **本项目（既定）** | blocker / concern / nit 三级 + 行级锚定结构化 JSON | 枚举标签 |

→ 本项目三级枚举与 CodeRabbit 同族（枚举标签派），且比其 5 级更收敛。**PR-Agent 的分数阈值机制值得借鉴为「nit 噪音控制」**：低于阈值不发布，`publish_output_no_suggestions` 可抑制「没有问题」的空评论。

### 4.3 输出格式约束

- **结构化 JSON 是趋势**：本项目的 findings 结构化 JSON（行级锚定 + 三级严重度）与业界「一键应用修复」的能力要求天然对齐——CodeRabbit 的建议可一键应用、PR-Agent 的 `commitable_code_suggestions`（可提交代码块）都依赖结构化输出而非自由文本。
- **数量约束**：PR-Agent `num_max_findings=3`（/review 关键问题上限）、`num_code_suggestions_per_chunk=3`、`final_clip_factor=0.8`——业界普遍**硬性限制发现数量**防噪音。
- **语言/持久化**：`response_language`（PR-Agent）、`language` + `tone`（CodeRabbit，100+ 语言）——输出语言是配置项不是提示词hack。
- **自审机制**：PR-Agent 有 `demand_code_suggestions_self_review`（让模型复审自己的建议）与 CodeRabbit 的 Verification agent——本项目的 Gatekeeper 汇总节点可参考。

### 4.4 模式分化：Mentor/Gatekeeper 的先例

- **CodeRabbit profile 机制**（quiet/chill/assertive）证明「同一发现管道，不同呈现参数」是成熟产品形态——对本项目「两模式共享 schema、渲染层分化」是最直接的业界背书。
- **审查风格引导**（Copilot 教程 Review Style 节）：「Be specific and actionable / Explain the why / Acknowledge good patterns / Ask clarifying questions when intent is unclear」——这份清单几乎就是 Mentor Mode 的提示词骨架（教「为什么」、承认好模式、意图不明时提问）。
- **不对称性论证**（CodeRabbit 引 Self-Correction Bench，NeurIPS 2025 workshop）：14 个模型平均 64.5% 的自我纠错盲区——**作者自审是弱验证**，这为「Pre-check（Mentor）不能替代 Review（Gatekeeper）」的产品逻辑提供了论据。

---

## 5. 上下文策略清单（token 成本 × 架构视野覆盖面，按成本升序）

| # | 策略 | 注入时机 | token 量级 | 架构视野覆盖面 | 先例 | 备注 |
|---|---|---|---|---|---|---|
| 0 | 纯 diff | 每次 | 随 PR（1k–10k+） | **无** | 所有工具的底线输入 | 被微软 2013 研究 + Cloudflare 三类失效模式证伪为不充分 |
| 1 | diff + hunk 行扩展 | 每次 | +每 hunk 5–10 行 | 函数局部 | PR-Agent | 最便宜的「视野」，但只有函数级 |
| 2 | diff + 动态扩展到外层函数/类 | 每次 | 小（≤10 行/hunk 增量） | 函数/类结构边界 | PR-Agent（`allow_dynamic_context`） | 消除「hunk 切在函数中间」的失明 |
| 3 | **结构地图常驻**（目录树+模块职责） | 常驻系统上下文 | **小：≤500 行 ≈ 1–2k** | **仓库级（职责/分层视野）** | PR-Agent（AGENTS.md 默认注入）、Copilot（instructions）、CodeRabbit（code guidelines 自动拾取） | 人工/LLM+人工维护；含 Spec KB 槽位可复用同一基建 |
| 4 | 符号级 repo map（tree-sitter + PageRank） | 常驻或按需 | 小–中：默认 1k（可调至 8k） | 仓库级（API 面 + 符号依赖全局排序） | aider | 全自动；与 #3 互补（符号 vs 语义职责） |
| 5 | **import 邻域按需拉取**（改动文件直接依赖的签名） | 按需 | **中：0.5k–3k（估算）** | **直接依赖层（一跳）** | aider 引用图（邻域边权重 ×50） | 本项目第二档；业界无轻量工具做二跳 |
| 6 | agentic 全仓库探索 | 按需（agent 自主） | 中–大：$0.05–5/次（Copilot 定价） | 无界（agent 决定） | Copilot（默认启用）、CodeRabbit（agentic exploration） | 视野最大但成本与延迟不可预测；依赖 Actions/沙箱基建 |
| 7 | graph-based RAG / 向量语义索引 | 基建 + 检索注入 | 中（检索切片） | 全库结构化（AST+数据流+依赖图） | CodeRabbit context engine、Sweep（向量）、JetBrains Context | 重基建路线；**本项目已明确排除**，列为对照 |

**Pareto 判断**：以「token 成本 × 架构视野」衡量，**#3 + #5 组合（本项目两档方案）落在低成本-中高覆盖的前沿上**：常驻 1–2k + 按需 0.5–3k，总增量约 2–5k tokens，即可覆盖「仓库分层职责 + 改动直接影响面」，而无需 #6 的不可控成本或 #7 的索引基建。#1/#2（hunk 扩展）可作为几乎免费的第三层补充（PR-Agent 证明其默认值得开）。

---

## 6. 对两档方案的具体建议

### 6.1 结构地图（第一档）

1. **形态**：目录树 + 每目录/模块一行职责 + 构建/测试命令 + 项目特有约定。粒度止于模块级，**不做逐文件描述**（Claude Code 明确列为反模式）。
2. **规模**：≤500 行（借用 PR-Agent `repo_context_max_lines` 锚点），预算观 1–2k tokens。
3. **生成流程**：LLM 生成初稿（对齐 Claude Code `/init` 的「起点」定位）→ 人工校对合入 → 维护信号驱动修剪（agent 重复违规 = 地图太长或措辞歧义，Anthropic 的调优信号清单可直接搬）。
4. **命名**：文件采用 `AGENTS.md` 标准（22+ 工具识别、CodeRabbit/PR-Agent/Copilot 自动拾取），为规格赢得生态兼容叙事。
5. **与 Spec KB 分离**：不同注入槽位、同一注入基建；Spec KB 为空时该槽位整体缺席（对齐 CONTEXT.md「no spec, no verdict」）。

### 6.2 import 邻域（第二档）

1. **提取**：Python MVP 用 stdlib `ast`（零依赖、语法正确）；多语言远期走 tree-sitter tags（可直接复用 aider 的 37 语言 `.scm` 查询文件）；**不采用纯正则**（边界 case 多且无业界先例）。
2. **邻域定义**：一跳双向——改动文件 import 的符号（下游）+ 引用了改动文件所导出符号的文件（上游影响面）；aider 的 ×50 边权重证明上游（谁依赖我）对审查尤其重要（跨系统影响漏检是 diff-only 三大失效之一）。
3. **注入内容**：符号签名行（def/class + 参数），非文件全文；hunk 可顺带做 ±5 行/动态到外层函数的扩展（PR-Agent 默认值，几乎免费）。
4. **预算**：独立预算 1–2k tokens 起步；超限按引用权重排序截断（aider 式），高频标识符做 sqrt 阻尼防止 `logging` 类符号挤爆预算。
5. **失效安全**：解析失败的文件跳过而非崩溃（aider 用 pygments 兜底的分层思想）。

### 6.3 对下游票的直接输入

**→ #8 专家团 roster 票**：

- 每个专家的提示词 = 目标声明（Copilot 式一句话角色）+ 维度开关（PR-Agent 式 require_*，映射为「该专家检什么/不检什么」）+ findings JSON schema 约束（三级严重度 + 行级锚定）。
- 严重度三级（blocker/concern/nit）与 CodeRabbit 五级同族且更收敛；nit 建议带 PR-Agent 式发布阈值防噪音。
- 汇总节点（Supervisor fan-in）可参考 PR-Agent self-review / CodeRabbit Verification agent 的「复审自己产出」模式。
- 模式分化用 profile 参数（CodeRabbit quiet/chill/assertive 先例），不动发现管道——与既定「共享 schema、渲染层分化」决策互证。

**→ #9 业务逻辑一致性原型票**：

- 实验设计输入：业务逻辑一致性 = 最依赖架构视野的检查类别（CodeRabbit 论证链：宏观缺陷漏检根因是 diff 不携带 API 契约与下游消费者信息）。
- 最小充分上下文假设：diff + 结构地图 + import 邻域 + Spec KB 条目。
- 对照组建议：纯 diff / diff+地图（无邻域）/ diff+地图+邻域 三组，golden set 验证邻域的**边际增益**——这正是两档方案中第二档存在价值的直接检验。
- 已知上限：一跳邻域抓不到「时序/并发类」缺陷（Cloudflare 第三类失效），原型预期应聚焦前两类（架构感知、跨系统影响）。

---

## 7. 方法论与取证限制

- **一手来源优先**：aider（文档+博客+源码）、PR-Agent（configuration.toml 原文）、GitHub Copilot（官方文档）、CodeRabbit（官方文档+官方论证文章）、AGENTS.md/Claude Code（官方标准/文档）、JetBrains（官方博客）。
- **未核实项**（如实标注）：
  - Sweep 官方博客（blog.sweep.dev）返回 402，Wayback 超时，其上下文引擎博客细节未转述；Sweep 结论仅基于 DeepWiki 源码级索引与 GitHub 仓库页。
  - Claude Code `#` 快捷键机制位于另一文档页（Store instructions and memories），本次未抓取，未引用。
  - §2.4 的 import 邻域 token 估算为推算值，已标注，锚点数据均有出处。
  - aider 博客不含性能基准数据，本文未引用任何 aider 性能数字。
  - CodeRabbit 的质量数据（−35% 审查时间等）为其自报案例，来源已注明。
- **二级研究的处理**：微软 2013（Bacchelli & Bird）、Google 2018（Sadowski et al.）、Lost in the Middle、Self-Correction Bench 等学术结论均转引自 CodeRabbit 官方论证文档（https://www.coderabbit.ai/guides/code-context），未回溯原文，引用时请注明转引。

## 8. 参考来源汇总

1. aider repo map 文档 — https://aider.chat/docs/repomap.html
2. aider 博客《Building a better repository map with tree sitter》 — https://aider.chat/2023/10/22/repomap.html
3. aider `repomap.py` 源码 — https://github.com/Aider-AI/aider/blob/main/aider/repomap.py
4. aider 支持语言 — https://aider.chat/docs/languages.html
5. CodeRabbit 架构 — https://docs.coderabbit.ai/overview/architecture
6. CodeRabbit《Code context: The evidence behind trustworthy AI code review》（2026-06-24） — https://www.coderabbit.ai/guides/code-context
7. CodeRabbit 审查流程 — https://docs.coderabbit.ai/guides/code-review-overview
8. CodeRabbit 配置参考 — https://docs.coderabbit.ai/reference/configuration
9. CodeRabbit path instructions — https://docs.coderabbit.ai/configuration/path-instructions
10. CodeRabbit code guidelines — https://docs.coderabbit.ai/knowledge-base/code-guidelines
11. GitHub Copilot code review 概览 — https://docs.github.com/en/copilot/concepts/agents/code-review
12. GitHub Copilot 自定义指令教程 — https://docs.github.com/en/copilot/tutorials/customize-code-review
13. GitHub Copilot 仓库自定义指令配置 — https://docs.github.com/copilot/how-tos/use-copilot-agents/request-a-code-review/configure-coding-guidelines
14. PR-Agent `configuration.toml` — https://github.com/qodo-ai/pr-agent/blob/main/pr_agent/settings/configuration.toml
15. PR-Agent（PyPI，AGENTS.md 默认注入说明） — https://pypi.org/project/pr-agent/
16. PR-Agent 配置参考（DeepWiki） — https://deepwiki.com/qodo-ai/pr-agent/7.1-configuration-reference
17. Sweep 仓库 — https://github.com/sweepai/sweep
18. Sweep（DeepWiki 源码索引） — https://deepwiki.com/sweepai/sweep
19. Claude Code 最佳实践 — https://code.claude.com/docs/en/best-practices
20. AGENTS.md 标准 — https://agents.md/
21. JetBrains Context 发布博客（2026-07） — https://blog.jetbrains.com/ai/2026/07/introducing-jetbrains-context-repository-intelligence-for-coding-agents/
