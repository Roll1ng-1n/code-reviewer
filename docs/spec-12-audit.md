# 总规格 #12 完成核对

依据 GitHub Issue #12 的 27 条 User Stories 与命名契约逐项核对。此表记录能力和行为测试，不把假模型测试当作真实模型质量评估。

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| 1 一条命令进行预检 | `precheck` Git 输入与 CLI Report；`tests/test_git_input.py` | 已实现，需要模型配置 |
| 2 导师式解释和建议 | Mentor 渲染显示 rationale/suggestion；`tests/test_cli_human_render.py` | 已实现，建议字段按契约可选 |
| 3 预检默认不联网 | 非空 diff 仍使用 DeepSeek 远程模型，未有离线模型方案 | **未完成，等待结项范围答复** |
| 4 严重度分组和 nit 折叠 | Gatekeeper 渲染与 `--show-nits`；`tests/test_render.py` | 已实现 |
| 5 架构维度 | architecture charter、确定性路由；`tests/test_expert_panel.py` | 已实现，模型效果不由脚本化测试证明 |
| 6 CI 退出码 | pass/blocked/concerns → 0/1/2；`tests/test_cli_exit_codes.py` | 已实现 |
| 7 自动发现 Spec KB | 约定目录、配置与 CLI 三层；`tests/test_spec_kb.py` | 已实现 |
| 8 空库不编造规范 | 两臂剔除 spec 维度；`tests/test_baseline_regressions.py` | 已实现 |
| 9 章节引用 | spec charter 与两臂相同 KB 输入；`tests/test_spec_kb.py` | 已实现提示词契约，引用正确性仍由模型决定 |
| 10 规范指纹 | 最终库数量与内容 hash；`tests/test_safety_regressions.py` | 已实现 |
| 11 文件和新侧行锚定 | Finding schema 与专家输出契约；`tests/test_cli_report_contract.py` | 已实现；定位质量另记录行号分层 |
| 12 三级严重度锚点 | 专家提示词与程序化 Verdict；`tests/test_verdict_derivation.py` | 已实现 |
| 13 同一 Report 双渲染 | `_emit` 消费统一 Report；`tests/test_cli_report_contract.py` | 已实现 |
| 14 固定 diff 管道回放 | `--diff-file -` 从 stdin 按 UTF-8 读取；`tests/test_stdin_replay.py` | 本轮补齐 |
| 15 描述或提交意图 | 显式描述优先、提交信息回退；`tests/test_git_input.py` | 已实现 |
| 16 非 Python 仓库配置 | cwd `.reviewer.yaml`，不依赖打包标记；`tests/test_config.py` | 已实现 |
| 17 密钥来自环境变量 | `make_provider` 配置校验；`tests/test_config.py` | 已实现 |
| 18 可替换模型抽象 | `ModelProvider`、ScriptedProvider 和 LangChain 适配器 | 已实现；产品工厂当前仅 DeepSeek |
| 19 确定性路由 | 配置过滤、空库规则与 Send；`tests/test_expert_panel.py` | 已实现 |
| 20 空库不启动 spec | `enabled_experts` 可观测；`tests/test_baseline_regressions.py` | 已实现 |
| 21 聚合保守合并 | 异常整组保留，未知复核 id 整批保留；`tests/test_aggregator.py`、`tests/test_safety_regressions.py` | 已实现 |
| 22 严重度只降不升 | 混合严重度和越界升级护栏；`tests/test_safety_regressions.py` | 已实现 |
| 23 style 产出上限 | 两臂共用 nit/数量护栏，保留前 3 条；`tests/test_baseline_regressions.py` | 已实现数量限制；不是模型排序后的 top-3 |
| 24 产品双臂配对 | 相同输入配置/模型/KB/charter/护栏；`tests/test_product_evaluation.py` | 已实现能力，默认重复 3 次 |
| 25 一键 P/R | 产品配对脚本保存 precisions/recalls/f1s；`tests/test_evaluation_metrics.py` | 本轮补齐产品指标 |
| 26 干净 PR 独立误报指标 | `clean_fp_counts`、`clean_fp_per_pr`、`clean_pr_false_positive_rates` | 本轮补齐 |
| 27 文件约束加语义匹配 | 一对一 judge，行号只作分层；`tests/test_evaluation_metrics.py` | 已实现 |

## 命名实现与契约

baseline 使用 `langchain.agents.create_agent`，通过 `ProviderChatModel` 委托同一 ModelProvider；不配置工具，不改用第二个模型客户端。两臂仍共享 schema、charter、上下文、启用维度、聚合与渲染。CLI 测试验证融合专家每次仅请求一次 provider。`pyproject.toml` 明确声明 LangChain 依赖。

共享状态使用 TypedDict，专家增量保留 `Annotated[list, operator.add]`，节点内校验 Finding。最终聚合确定性排序编号为 `F001..`，Report 维持 schema-v1。交互确认继续只在显式 `--interactive` 时启用。

三分类评估中，已配对命中为 CONFIRMED；未配对 Finding 再基于实际 diff、描述和标注进行三分类，保留判据。未命中不自动等于 FABRICATED。重复真实问题可分类为 CONFIRMED，但一对一 TP 仍只计一次。分类失败沿用整轮中止及旧结果保护。

`clean_fp_per_pr` 为干净 PR 上 Findings 数 / 干净 PR 数；`clean_pr_false_positive_rates` 为至少一条 Findings 的干净 PR 数 / 干净 PR 数；没有干净样本时两个比例为 null。`hallucination_rates` 为 FABRICATED / 全部 Findings。匹配行号差 ≤3 记录 `within_3`，更远记 `outside_3`，缺少有效行号记 `unavailable`，三者均不影响语义 TP。

golden set v2 的 13 个真实 PR、14 条标注和 3 个干净对照保留原资产及历史结果，本轮不伪造新的真实模型分数。judge 仍为 deepseek-chat，同族限制保留。PR 集成、RAG、模型分级、生产化及其他明确 Out of Scope 项不作为本票关闭的附加要求。

## 结项条件

#12 尚不能按原规格全部完成关闭：User Story 3 未实现。已请求用户决定按现有 MVP 结项并将离线预检记录为后续需求，或继续落实离线预检；尚未收到答复。该答复不会由自动继续、时间经过或绿色测试代替。
