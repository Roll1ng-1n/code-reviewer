# 调研：LLM 代码审查的评估方法论（Issue #4）

> 调研日期：2026-08-29 ｜ 调研员：researcher-r3 ｜ 服务对象：任务票 #10（golden set 评估集构建）、#11（对照实验）
> 项目背景：LangGraph 单图双模式（Mentor/Gatekeeper）AI 代码审查助手，评估方案初定为 golden set 为主（10-20 个真实 PR + 人工标注，含少量干净 PR 对照）。

---

## 一、Golden Set 构建：代码审查 AI 评估集的建法

### 1.1 三种公开实践路径

**路径 A：真实 PR 回放 + 人工标注（业界主流，与本项目初稿一致）**

| 基准 | 规模 | 构建方法 | 标注 |
|---|---|---|---|
| Martian code-review-benchmark（离线） | 50 个 PR / 5 个仓库（Sentry-Python、Grafana-Go、Cal.com-TS、Discourse-Ruby、Keycloak-Java） | fork 真实 PR → 让被测工具评审 → 与 golden comments 对比 | 每条 golden comment 人工确认为"真实评审者应发现的问题"，带 Low/Medium/High/Critical 四级严重度 |
| SWR-Bench | 1000 个 PR / 12 个 Python 项目（取自 SWE-bench 仓库） | 21,000+ 候选 PR → 过滤（剔除无评审评论、>10 commits 的 PR）→ LLM 识别"变更动作"（人类指出且后续被开发者实际修复的问题，11 类分类法）→ SZZ 算法 + 分层抽样做统计平衡 → 500 Change-PR + 500 Clean-PR | 5 名有经验标注者，Cohen's κ=66.08（高度一致），分歧集体讨论解决 |
| SWE-PRBench | 350 个 PR / 65 个仓库（700 候选筛掉一半） | 十阶段过滤流水线：RQS 仓库质量分（实质性人工评论频率、测试质量、活跃度；**惩罚高星仓库降低训练数据污染**）+ RVS 评审价值分（多条实质性评论线程、多独立评审者、含 bug 修复信号；门槛 RVS≥0.35） | ground truth = 真实人工评审者留下的评论；P0/P1/P2 严重度；按"识别问题所需证据位置"分 Type 1（diff 内，34%）/ Type 2（同文件未改动代码，40%）/ Type 3（跨文件，26%） |

来源：
- https://github.com/withmartian/code-review-benchmark
- https://arxiv.org/abs/2509.01494（SWR-Bench，西北工业大学 & 北大）
- https://arxiv.org/abs/2603.26130（SWE-PRBench）

**路径 B：注入缺陷（合成 ground truth）**
- SWE-smith（NeurIPS 2025 D&B Spotlight）：把任意 GitHub 仓库变成"SWE-gym"，通过合成缺陷注入生成"近乎无限"的任务实例（文件定位、程序修复、SWE-bench 式任务）。来源：https://github.com/SWE-bench/SWE-smith
- 优点：ground truth 完全可控（确切知道缺陷位置、类型、严重度），可规模化，天然提供"干净 vs 缺陷"对照。
- 风险：合成缺陷分布偏离真实缺陷。SWR-Bench 专门用 SZZ 算法 + 分层抽样**防止模型靠简单启发式（如修改行数）猜出哪个 PR 含缺陷**——说明"注入痕迹可被识破"是此路径的已知风险。
- 结论：适合作为真实 PR 的**扩充分量**（如把干净 PR 按可控方式注入已知缺陷），不宜作为唯一来源。

**路径 C：以真实修复行为 ground truth（免标注，在线/半在线）**
- Martian 在线基准：经 GitHub Archive（BigQuery）持续采样近 7 天有 bot 评论的真实 PR（防训练数据泄漏），以**开发者 review 后的实际修复 commit** 为 ground truth；LLM 三步分析：提取 bot 建议（含类别与严重度）→ 提取人类实际修复行为 → 匹配判定。
- 优点：零标注成本、持续更新、天然抗 game（静态基准可被针对性优化）。
- 缺点：只能度量"建议被采纳"类 precision 指标，无法度量召回（人类没修不等于没说对）。Martian 因此采用"离线静态 + 在线动态"双基准互检。
- 来源：https://github.com/withmartian/code-review-benchmark

### 1.2 规模与标注成本经验值

- **Anthropic 多智能体研究系统的建议**：起步测试集 20-50 个来自真实失败的简单任务；每次发布前审查固定困难查询集 + 跑约 20 个代表性查询；单维度 ≥0.7 算通过。来源：https://agentpatterns.ai/workflows/llm-as-judge-evaluation/（依据 Anthropic 多智能体研究实践）
- **JavaGuide 评测体系**：第一版 20-50 条仅用于验证评测流程，**无法支持统计结论**；"分布比总量更重要"（200 条同类不如 100 条覆盖 10 类）；Agent 场景需重复运行，分别记录 pass@k（至少一次成功，能力上限）与 pass^k（连续成功，生产稳定性）——单次成功率稳定 90% 时连续 5 次全成功概率仅约 59%。来源：https://javaguide.cn/ai/llm-basis/llm-evaluation.html
- **SWR-Bench 标注投入**：5 名标注者处理 1000 个 PR（有 2.1 万候选做机器过滤在前）；标注者间 κ=66.08。
- **Martian**：未披露标注成本；"接入一个新被测工具约一个下午"。
- **HF 评估指南**：LLM judge 单次评估成本 $0.001-0.01 vs 人工 $1-10+；LLM judge 可扩展到千至万级，人工只到十至百级。来源：https://deepwiki.com/huggingface/evaluation-guidebook/2.3-llm-as-a-judge
- **CI 成本**：跑 500 条 LLM-as-judge 评测约 10-30 分钟，PR 阶段只跑核心回归集。来源：https://javaguide.cn/ai/llm-basis/llm-evaluation.html

### 1.3 对本项目（10-20 个真实 PR 初稿）的校验结论

1. **方向正确**：真实 PR 回放 + 人工标注是三个最新基准（Martian/SWR-Bench/SWE-PRBench）共同的主干路径；"含少量干净 PR 对照"与 SWR-Bench 的 500/500 Change/Clean 平衡设计同源（干净 PR 用于测假阳性率，防模型乱报）。
2. **规模偏小但可用**：10-20 个 PR 处于 Anthropic 建议（20-50）与 JavaGuide 首版（20-50）的下限以下。可行性补救：(a) 每个维度/类型至少 2-3 个样本做分层（逻辑缺陷/资源泄漏/安全/性能/演进建议 × Type 1-3 难度）；(b) 所有比例指标附 Wilson 置信区间，避免过度解读；(c) 预留失败案例回填机制让集合增长。
3. **建议引入的两个增量**：(a) 注入缺陷扩充分量（可控 ground truth、便宜）；(b) 用"干净 PR 上零 findings"作为单独门禁指标。
4. **标注成本预估**：参考 SWR-Bench（5 人标 1000 PR）与 Martian（50 PR 全人工），10-20 个 PR、每个 PR 3-8 条 golden findings 的标注量约为**数人时级别**（一个熟悉代码的标注者 1-2 天），可行。

---

## 二、指标：行级锚定 findings 的精确率/召回率

### 2.1 业界关键发现：语义级匹配，不做行级精确匹配

- **Martian**：Precision = 匹配到 golden comment 的工具评论数 / 工具评论总数；Recall = 被工具找到的 golden comments / golden comments 总数。匹配由 LLM judge 判定"**是否描述同一底层问题**"（do these describe the same underlying issue）——语义级，允许措辞不同。README **明确没有行级匹配或位置容差机制**；靠去重步骤（step2_5）防止 inline 评论与 summary 重复计为假阳性；dashboard 支持可调 F-beta 权重。
- **SWR-Bench**：裁判 LLM 先从工具输出中解析出"预测 change-actions"并按 11 类分类法赋型，再判断每个 ground-truth 问题是否被任一预测动作**语义上识别**；TP/FP/FN → 标准 P/R/F1。**BLEU 与人工判断负相关**，证明文本相似度指标不适用。
- **SWE-PRBench**：judge 三分类 CONFIRMED（与人类识别同一底层问题）/ PLAUSIBLE（观察正确但不在 ground truth 中）/ FABRICATED（事实错误或引用不存在的代码=幻觉）；单 PR 复合得分 s 综合召回率、精确率、幻觉率；基准总分 = 难度加权平均；FPR（幻觉率）单独报告，各模型差异显著（0.193-0.417）。
- 工程佐证：阿里 Open Code Review 实践明确列出"**位置漂移：LLM 无法精准映射代码行号**"是一等工程难题。来源：http://chenxutan.com/d/3448.html

**结论**：行级精确匹配过于脆弱（LLM 行号映射天然漂移 + golden 行号本身标注粒度有限），业界最新实践全部退到**语义级 issue 匹配**，行号只作辅助信号。

### 2.2 推荐的分层匹配协议（本场景落地方案）

匹配一个 finding ↔ 一条 golden 标注需同时满足：
1. **文件级硬约束**：finding 锚定的文件与 golden 相同（文件不同直接不匹配——文件错位大概率是幻觉或另一个问题）；
2. **语义等价**（LLM judge）：判定"是否描述同一底层问题"；
3. **行级接近度作为质量分层信号（非通过/失败判据）**：行号差 ≤ 容差（建议 ±3 行，hunk 边界内）记"精确定位"，超出记"文件内定位"——两者都算匹配成功，但分开统计报告。

匹配为**二部图一对一匹配**（一个 finding 只能消耗一条 golden，反之亦然），用贪心或最优匹配求解，防止一条泛泛的 finding 匹配多条 golden。

### 2.3 指标公式

```
Precision = |matched findings| / |all findings|
Recall    = |matched goldens| / |all goldens|
F1        = 2PR/(P+R)；F-beta 可按业务调权（审查场景通常 R 权重高）

# 干净 PR 专属（SWR-Bench 式对照指标）
FP-on-clean rate = |findings on clean PRs| / |clean PRs|   # 理想为 0

# 幻觉率（SWE-PRBench 式，可由 judge 三分类导出）
Hallucination rate = |FABRICATED findings| / |all findings|

# 严重度混淆矩阵：行 = golden 严重度（Critical/High/Medium/Low），列 = 预测严重度（同 4 级 + none）
# 派生指标：
SeverityAccuracy        = 对角线元素和 / 总数
WeightedSeverityError    = Σ w(i,j)·M(i,j)，w 随严重度距离增大（低估 Critical 惩罚最重）
HighSevMissRate          = (golden=Critical/High 且未检出) / (golden=Critical/High 总数)   # 高权重失败，单独设门禁
```

统计注意事项：
- 样本小（10-20 PR）时所有比例报告 **Wilson 置信区间**；
- Agent 非确定性 → 每配置重复 n 次（≥3），报告均值±标准差与 pass@k / pass^k（来源：https://javaguide.cn/ai/llm-basis/llm-evaluation.html）；
- 高权重失败（如 Critical 漏检）不被均值稀释，单独设门禁（同上来源）。

---

## 三、LLM-as-judge：可靠性、已知偏差与缓解

### 3.1 与人工的一致性基准（可引用的数字）

| 研究 | 一致性 | 说明 |
|---|---|---|
| MT-Bench（Zheng et al., NeurIPS 2023 D&B） | 强 LLM judge（GPT-4）与人类专家一致性 **>80%**，与人类相互之间的一致率相当 | 3K 受控专家投票 + 30K 众包投票双验证 |
| SWR-Bench judge | 与人类专家原始一致率 **~90%**，κ∈[52.8%, 62.0%] | 结构化匹配任务（非开放式打分），一致率更高 |
| SWE-PRBench judge | **κ=0.75**（实质性一致） | 固定 rubric 三分类 |
| G-Eval（EMNLP 2023） | GPT-4 + CoT + 表单式打分，摘要任务 Spearman **0.514** | 开放式 NLG 打分上限参考 |

来源：
- https://arxiv.org/abs/2306.05685（MT-Bench）
- https://www.alphaxiv.org/abs/2509.01494（SWR-Bench）
- https://www.alphaxiv.org/abs/2603.26130（SWE-PRBench）
- https://aclanthology.org/2023.emnlp-main.153/（G-Eval）

**对本场景的含义**：判定"finding 是否等价于标注"属于**结构化、有参照（golden）、判定面窄**的任务（SWR-Bench 型），一致性显著高于开放式打分（G-Eval 型）——这是 LLM-as-judge 在本场景可用的最强论据。

### 3.2 已知偏差（量化证据）

| 偏差 | 证据 | 来源 |
|---|---|---|
| **位置偏差** | 交换 A/B 位置后 GPT-4 判断翻转（MT-Bench Fig.1）；Dartmouth 系统 study（15 个 judge、2 基准、22 任务、15 万实例）证明位置偏差**系统性非随机**，候选间质量差距是强影响因素，与 prompt 各部分长度弱相关 | https://arxiv.org/abs/2406.07791 |
| **冗长偏差** | GPT-3.5、Claude-v1 偏好更长答案即使内容冗余（"repetitive list"攻击实验）；GPT-4 抗性最强 | https://arxiv.org/abs/2306.05685 |
| **自我增强偏差** | 部分模型偏爱自己生成的回答；MT-Bench 中 GPT-4、Claude-v1 有一定自身偏好，GPT-3.5 无（论文称数据量有限） | 同上 |
| **有限推理能力** | judge 可能被上下文中的错误答案误导——GPT-4 能独立解题却在评分时算错 | 同上 |
| **LLM 偏爱 LLM 文本** | G-Eval 发现 LLM 评委系统性偏向 LLM 生成的文本 | https://aclanthology.org/2023.emnlp-main.153/ |

### 3.3 缓解措施（论文实证 + 工程实践汇总）

- **位置偏差**：成对比较必须交换顺序评两次、两次一致才判胜负（MT-Bench）；或改用单答案评分/单 finding 判定，从设计上消除位置变量（本场景天然适合后者——judge 一次只看一个 finding vs 一条 golden，无 A/B 对比）。
- **冗长偏差**：校准集放入"长而空 vs 短而准"对照样本（JavaGuide）。
- **自我增强**：judge 模型与被评 agent 模型**异族**（如被评用 GPT 系则 judge 用 Claude 系或反之）；跨厂商多 judge 抽样互检。
- **有限推理**：reference-guided——把 golden 标注、缺陷证据直接给 judge；让 judge 先独立陈述理由再下结论（CoT），但**最终只输出结构化 JSON**。
- **Rubric 锚点化**：每级严重度给出可观察定义（如 Martian 四级 / SWE-PRBench P0-P2 的定义方式）；允许 judge 输出 `unknown / needs_human_review`，不逼硬判（JavaGuide）。
- **HF 评估指南的元评估三步**：在样本上对比 judge 与人工标注；检查系统性偏差；多 judge 时测 judge 间一致性。来源：https://deepwiki.com/huggingface/evaluation-guidebook/2.3-llm-as-a-judge

### 3.4 人工抽检比例经验值

- **Anthropic 实践（经 AgentPatterns 整理）**：抽检不是固定比例随机抽样，而是**三层定向**：①每次发布审查固定困难集；②自动 judge 标记为 borderline 的输出必须人工审查；③轮换分布边缘的新颖查询。校准五步：定义维度 → 人工按 rubric 打分 → judge 打同一样本 → 比较分歧、细化 rubric → 持续分歧视为需调查信号。judge 与人类必须用**同一评分标准**。来源：https://agentpatterns.ai/workflows/llm-as-judge-evaluation/
- **学术前沿**（Jane Paik Kim, arXiv 2605.16354）：两阶段设计——LLM 全量评 + 人工评子样本；在 LLM 可预测性低的层分配更多人工；用双重稳健估计量做功效分析。这是"该抽多少"的形式化方向，落地可简化为分层抽检。来源：https://arxiv.org/abs/2605.16354
- **JavaGuide**：二分类看 **Cohen's kappa** 而非裸一致率（80% 一致率可能是类别基线假象）；结果记录含 pass/fail、score、reason、category、confidence。来源：https://javaguide.cn/ai/llm-basis/llm-evaluation.html
- **本场景建议**：初次建 golden set 时 10-20 个 PR 全量人工（规模小，可行且必要）；此后每次评估 LLM judge 全量 + 人工抽检 15-25%（borderline 全查 + 按严重度/类型分层定向抽），judge-人工不一致样本进校准集迭代 rubric。

---

## 四、工具链：LangSmith evals / promptfoo / DeepEval 适配度

### 4.1 三者能力对照（依据官方文档）

| 维度 | LangSmith | promptfoo | DeepEval |
|---|---|---|---|
| 定位 | trace/实验管理平台（SaaS，有免费层） | 声明式评测 CLI + 红队（开源，本地运行） | Pytest 原生评测框架（开源 + Confident AI 云） |
| 测试定义 | Python SDK（数据集 + evaluator 函数） | YAML/CSV 声明式（prompts + tests + providers） | Pytest 风格（`assert_test` + Golden 数据集） |
| LLM-as-judge | 内置 evaluator 类型（另有 human、code rules、pairwise） | `llm-rubric`、`g-eval`、`factuality` 等断言，可换 provider 与 rubricPrompt | GEval（criteria + CoT）、ConversationalGEval、轨迹指标 |
| 自定义逻辑 | 任意 Python evaluator | `javascript` / `python` 断言（内联或 file://），weight/threshold/metric 标签，assertScoringFunction 自定义聚合 | 自定义 Metric 类；threshold 门控 |
| 数据集管理 | UI + SDK；**生产 trace 一键转数据集**；实验对比（baseline、重复次数、并发、缓存） | 文件为主（CSV/YAML），Web UI 查看结果 | `EvaluationDataset`/`Golden`；云端回归对比（绿=改进红=退化） |
| CI | 支持（实验对比即回归测试） | 原生 CLI 设计，CI/CD 一等公民 | `deepeval test run` 即 pytest，天然入 CI |
| 语言 | Python（JS 亦有） | Node/Python 双栈，语言无关 | 仅 Python |
| 与 LangGraph 契合 | **同生态原生**：LangSmith 直接 trace LangGraph 应用，Studio 调试，trace→dataset 闭环 | 无 trace 观测，需自写 harness 调 agent | 无 trace 观测 |

来源：
- LangSmith：https://docs.langchain.com/langsmith/evaluation（四种 evaluator：human review / code rules / LLM-as-judge / pairwise；数据集来源三途径：手动策划、生产 trace、合成生成）
- promptfoo：https://www.promptfoo.dev/docs/intro/ 与 https://www.promptfoo.dev/docs/configuration/expected-outputs/
- DeepEval：https://deepeval.com/docs/getting-started

### 4.2 关键判断：本场景核心难点三家都不内置

行级容差匹配（二部图一对一 + 文件硬约束 + ±3 行分层）、严重度混淆矩阵、干净 PR 假阳性门禁——这些**都得自己写**。因此选型的真正变量是：**生态契合度（LangGraph 项目）** 与 **工作流契合度（CI 门禁 / pytest）**。

### 4.3 选型建议

- **主推：LangSmith（评估管理层）+ 自写 Python evaluator（匹配内核）**。理由：(a) 项目即 LangGraph，LangSmith 原生 trace 单图双模式的每次运行，调试期失败案例可一键转数据集（官方支持的生产 trace → 数据集闭环）；(b) 实验对比、重复次数、并发、缓存开箱即用，直接支撑 #11 对照实验（专家团 vs 单 Agent 同集对比）；(c) 自定义 evaluator 是任意 Python，行级容差与混淆矩阵照写。
- **替代：promptfoo + 手写断言**（若不想引入 SaaS 依赖）。`javascript` 断言写匹配内核，`llm-rubric` 做 finding 等价判定，`weight`/`metric` 组合出复合分，CLI 天然入 CI。Node/Python 双栈。
- **DeepEval 优先级最低**：除非团队已全面 pytest 化且想把指标即测试；其 GEval 与轨迹指标对本场景非必需。
- **手写脚本**：任何时候都要写匹配内核，差别只在管理/对比/可视化层。不建议纯手写（重复造实验对比与结果存储）。

---

## 五、对照实验（专家团 vs 单 Agent）的文献支撑

- **多评审聚合显著有效**（SWR-Bench）：n 个独立草稿评审 + 1 个聚合 LLM 合成最终报告，n=10 时 F1 提升 >43%（最高 43.67%），召回率翻倍以上；**小模型聚合胜过大模型单跑**（Gemini-2.5-Flash 5 次聚合 > Pro 单次）。来源：https://arxiv.org/abs/2509.01494
- 这直接支撑 #11"专家团 vs 单 Agent 基线"的实验假设：专家团（多角色评审 + 聚合）相对单 Agent 应在 F1/召回上有可测差异。
- 公平性要求：同一 golden set、同一 judge、同一匹配逻辑与容差、同样重复次数（≥3），报告均值±std 与置信区间。
- 现实参照（用于设定预期）：SWR-Bench 上前沿模型/工具最高 F1 仅 19.38%，SWE-PRBench 上 8 个前沿模型 diff-only 检出率仅 15-31%——**本项目自建小集上的绝对分数不重要，配对差异（同一把尺子量两个系统）才重要**。
- 主要失败模式供归因分析参考：SWR-Bench 中 48% 的误报源于缺乏上下文理解（建议的修复已在别处实现）；SWE-PRBench 中 Type 2（同文件上下文）随上下文增长检出崩塌、Type 3（跨文件）几乎不可检出。

---

## 六、评估方法论建议书

### 6.1 Golden Set 构建 SOP（供 #10 直接引用）

1. **规模与构成**：12-20 个真实 PR（Mentor/Gatekeeper 双模式各自覆盖），其中：
   - 10-15 个 Change-PR（含已确认问题）：按"问题类型 × 所需上下文类型"分层（逻辑缺陷/资源泄漏/安全/性能/演进建议 × Type 1 diff 内 / Type 2 同文件 / Type 3 跨文件），每格 ≥2 个样本；
   - 3-5 个 Clean-PR（干净对照，测假阳性率）；来源优先本仓库或同类 LangGraph/LLM 工具项目的真实历史 PR（有评审评论且评论引发修改的）。
2. **标注 schema**（每条 golden finding）：
   ```yaml
   - id: F001
     file: path/to/file.py
     line: 42            # 人工锚定行
     severity: high      # critical / high / medium / low（锚点定义见 6.2）
     category: logic     # 11 类可简化为 6-8 类
     context_type: T1    # T1 diff 内 / T2 同文件 / T3 跨文件
     description: "竞态条件：检查与写入非原子"
     evidence: "评审评论链接 + 后续修复 commit"
   ```
3. **标注流程**：1-2 名标注者初标 → 交叉复核 → 分歧讨论定稿（SWR-Bench 流程）；报告标注者间 κ。预估成本数人时（参考 1.2 节）。
4. **扩充分量（可选）**：对 Clean-PR 用受控方式注入 1-2 个已知缺陷（参照 SWE-smith 思路），获得 ground truth 完全可控的对照样本；注入样本单独标记，不与真实样本混算。
5. **维护**：每次评估发现的失败案例（漏报/误报/borderline）经人工确认后回填；Golden set 版本 + agent 版本 + prompt 版本一起记录（JavaGuide 实践）。

### 6.2 指标体系（供 #10/#11 引用）

核心（每模式 + 总体分别报告）：
- `Precision / Recall / F1 / F-beta`：匹配协议 = 文件硬约束 + judge 语义等价 + 一对一二部图匹配；行号 ±3 行内为"精确定位"分层信号。
- `FP-on-clean rate`：干净 PR 上 findings 数（理想 0）。
- `Hallucination rate`：judge 判为 FABRICATED 的比例（引用不存在代码/事实错误）。
- 严重度混淆矩阵（4×5）+ `SeverityAccuracy`、`WeightedSeverityError`、`HighSevMissRate`（单独门禁）。
- 稳定性：每配置重复 ≥3 次，报告均值±std、pass@k / pass^k。
- 所有比例附 Wilson 95% 置信区间。

### 6.3 Judge 提示词草案

**Judge A：finding ↔ golden 等价判定（单 finding，无 A/B 位置偏差面）**

```
你是代码审查评估专家。判断下面一条【候选 finding】是否与【参考标注 golden】
描述同一底层问题。

## 判定标准
- CONFIRMED：两者指向同一底层问题（根因相同），措辞、严重度、行号可以不同；
  行号偏差 ≤3 行视为同一位置。
- PLAUSIBLE：候选观察到的现象真实存在（引用的代码确实在 diff 中），但与
  golden 不是同一问题（golden 未覆盖的有效新发现）。
- FABRICATED：候选引用了不存在/被修改的代码，或对代码行为的断言错误。
- UNKNOWN：信息不足以判断。

## 输入
[diff hunk（带行号）]
[golden：文件、行、严重度、描述、证据]
[候选 finding：文件、行、严重度、描述]

## 输出（仅 JSON，不要展开推理过程）
{"verdict": "CONFIRMED|PLAUSIBLE|FABRICATED|UNKNOWN",
 "same_underlying_issue": true|false,
 "line_offset": <候选行-golden行 的整数差，同文件时填写，否则 null>,
 "reason": "≤30字"}
```

**Judge B：严重度合规复核（可选，锚点式）**

```
对以下已判定 CONFIRMED 的 finding，核对 agent 标注的严重度是否与下列锚点一致：
- critical：可被利用的安全漏洞 / 数据损坏 / 必然崩溃
- high：特定输入下功能性错误 / 资源泄漏 / 竞态
- medium：边界条件缺陷 / 健壮性缺失 / 明确的性能反模式
- low：风格、命名、可维护性建议
输出 {"severity_ok": true|false, "suggested": "...", "reason": "≤30字"}
```

工程约束（来自调研的偏差缓解）：
- judge 模型与被评 agent 模型异族；每次评估固定 judge 模型并随结果报告；
- 不做 A/B 成对比较（消除位置偏差面）；rubric 锚点化；允许 UNKNOWN 转人工；
- 记录 verdict + reason + confidence，人工抽检时可直接消费。

### 6.4 人工抽检方案

- 初建 golden set：100% 人工（10-20 PR 规模可行）。
- 常规评估：LLM judge 全量 + 人工抽检 15-25%，定向分层：borderline/UNKNOWN 全查；每严重度×类型至少抽 1；judge-人工不一致样本进校准集。
- 一致性用 Cohen's kappa 报告（不裸报一致率）；judge 校准五步（定义维度→人工打分→judge 打分→消歧→分歧调查）。

### 6.5 工具选型结论

- **主方案**：LangSmith 做实验管理层（LangGraph 原生 trace、trace→dataset 闭环、实验对比支撑 #11 对照实验）+ 自写 Python evaluator 实现匹配内核与全部指标。
- **轻量替代**（避免 SaaS 依赖时）：promptfoo（YAML 用例 + `javascript` 断言写匹配内核 + `llm-rubric` 做 judge 判定）+ CI 门禁。
- DeepEval 仅在团队强 pytest 偏好时考虑。
- 无论选谁，匹配内核（二部图匹配 + 容差 + 混淆矩阵）都是自写代码，属一次性投入（预计 300-500 行 Python）。

---

## 附：来源清单

| 主题 | 来源 |
|---|---|
| SWR-Bench（1000 PR、500/500 平衡、5 标注者 κ=66.08、judge ~90% 一致、聚合 +43% F1、最高 F1 19.38%） | https://arxiv.org/abs/2509.01494 |
| Martian code-review-benchmark（50 PR/5 仓库、语义匹配、严重度四级、在线基准以修复 commit 为 GT） | https://github.com/withmartian/code-review-benchmark |
| SWE-PRBench（350 PR、RQS/RVS 过滤、P0-P2、Type 1-3、CONFIRMED/PLAUSIBLE/FABRICATED、κ=0.75、检出 15-31%） | https://arxiv.org/abs/2603.26130 |
| MT-Bench（GPT-4 judge 与人 >80% 一致、三大偏差及缓解：swap/few-shot/CoT/reference-guided） | https://arxiv.org/abs/2306.05685 |
| G-Eval（CoT+表单式、Spearman 0.514、LLM 偏爱 LLM 文本） | https://aclanthology.org/2023.emnlp-main.153/ |
| 位置偏差系统研究（15 judge、150k 实例、系统性非随机、质量差距强相关） | https://arxiv.org/abs/2406.07791 |
| 人工抽检两阶段/分层（LLM 全量 + 人工子样本、双重稳健估计） | https://arxiv.org/abs/2605.16354 |
| Anthropic 20-50 起步、三层定向抽检、校准五步、0.7 阈值 | https://agentpatterns.ai/workflows/llm-as-judge-evaluation/ |
| HF 评估指南（偏差定性、judge 成本 $0.001-0.01、元评估三步） | https://deepwiki.com/huggingface/evaluation-guidebook/2.3-llm-as-a-judge |
| JavaGuide 评测体系（20-50 首版、分布>总量、pass@k/pass^k、kappa 不裸一致率、四类偏差缓解表、Judge prompt 模板） | https://javaguide.cn/ai/llm-basis/llm-evaluation.html |
| SWE-smith（合成缺陷注入、仓库→SWE-gym） | https://github.com/SWE-bench/SWE-smith |
| LangSmith 评估文档（四类 evaluator、数据集三来源、实验对比） | https://docs.langchain.com/langsmith/evaluation |
| promptfoo 文档（断言类型全表、llm-rubric、javascript/python 断言、CI） | https://www.promptfoo.dev/docs/configuration/expected-outputs/ |
| DeepEval 文档（GEval、pytest 集成、EvaluationDataset/Golden） | https://deepeval.com/docs/getting-started |
| 阿里 Open Code Review（LLM 行号映射漂移工程问题） | http://chenxutan.com/d/3448.html |
