# Issue #2 调研结论：LangGraph Python 编排能力边界（执行摘要）

**结论**：LangGraph 1.2.11（当前稳定版，2026-08-11 发布）对「Supervisor + 专家团」拓扑所需的全部关键能力均为原生支持，**无能力缺口**；风险集中在 3 个工程坑位，均可在实现规格中显式规避。

1. **版本现状**：稳定版 `langgraph 1.2.11`（Python ≥ 3.10）；1.0（2025-10）为稳定性发布，2.0 前无破坏性变更承诺；1.0 唯一硬性破坏 = 弃 Python 3.9；`create_react_agent` 已弃用 → `langchain.agents.create_agent`；`langgraph-supervisor` 库不再主推（推荐直接用工具调用/自建路由节点实现 Supervisor）。
2. **动态专家团（核心）**：条件边函数运行时返回 `[Send("expert", {...}) for ...]`，分支数与目标完全由运行时数据决定——「按 PR 改动内容启用专家、Spec KB 为空则跳过规范专家」即 Send 的标准用例；fan-in 用 `findings: Annotated[list, operator.add]` reducer 合并，聚合节点在全部并行专家完成后自动执行。
3. **三大坑（规格必须写死对策）**：① 并行写同一 key 必须声明 reducer（默认覆盖会静默丢失 findings）；② 并行 superstep 更新顺序不保证 → 聚合/裁决节点必须重排序（文件+行号+严重度）；③ `interrupt()` 恢复时节点从头重跑 → 中断前副作用必须幂等。
4. **子图**：编译子图可直接 `add_node`（共享 key 透传、私有 key 封装）；默认 per-invocation 模式继承父 checkpointer 且中断自动传播到顶层；但单步 LLM 专家用普通节点即可，仅内部多步流程的专家（如 Spec 检索+对照）值得子图化。
5. **CLI 人在回路（Gatekeeper）**：`interrupt()` + `Command(resume=...)` 是官方文档明确给出的 CLI 交互模式（含 `input()` 循环示例）；前提 = checkpointer（本地 `SqliteSaver`，单独安装 `langgraph-checkpoint-sqlite`）+ `thread_id`；静态断点仅限调试，HITL 应使用 `interrupt()`。
6. **流式（CLI 进度渲染）**：`stream_mode="updates"`（逐节点进度）+ `get_stream_writer()`/`custom`（自定义进度）+ `messages` 或 `stream_events(version="v3")`（逐 token，1.2 beta、官方推荐新应用）；注意：非 LangChain LLM 只能用 custom 模式转发 token，异步 + Python<3.11 需 `writer: StreamWriter` 参数注入。
7. **State 惯例**：TypedDict 为官方首选（Pydantic state 性能更低且 `create_agent` 不支持）；`Finding` 结构化校验放在专家节点内用 Pydantic 模型完成、输出侧可用 v2 invoke 自动转换。

完整报告（30 行能力-模式对照表 + 全部一手来源链接）：`research/langgraph-orchestration/findings.md`
