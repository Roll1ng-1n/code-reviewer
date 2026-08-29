# LangGraph Python 编排能力边界 — 调研报告

- **Issue**: [#2 调研：LangGraph Python 编排能力边界](https://github.com/Roll1ng-1n/code-reviewer/issues/2)
- **调研日期**: 2026-08-29 · **调研人**: researcher-r1（wayfinder-research 团队）
- **调研对象**: LangGraph **Python** 版，以 2026-08 最新稳定版 **langgraph 1.2.11**（2026-08-11 发布于 PyPI，Python >= 3.10）为准
- **调研方法**: 仅采信一手来源——官方文档（docs.langchain.com、reference.langchain.com）、官方源码（langchain-ai/langgraph 仓库）、PyPI 发布记录；二手资料仅用于线索定位，不作结论依据
- **用途**: 为拓扑设计「专家团票」（#8：roster 与聚合裁决）提供事实基础——Supervisor + 专家团（含单 Agent 基线对照）、CLI 预检优先、Spec KB 可注入可留空、findings 三级严重度/行级锚定

---

## 0. 一页结论

LangGraph 1.2.11 对本项目所需的全部关键编排能力**均为原生支持**，没有能力缺口：

1. **动态专家团 fan-out**：条件边函数在运行时返回 `[Send(节点, 独立状态), ...]`，分支数量与目标都由运行时数据决定（「按 PR 改动内容启用专家」即其标准用例）。
2. **fan-in 聚合**：`findings: Annotated[list, operator.add]` reducer 自动合并 N 个并行专家的输出；聚合节点在所有并行任务完成后执行。
3. **子图封装**：编译后的子图可直接 `add_node`；默认 per-invocation 模式继承父图 checkpointer，`interrupt()` 自动传播到顶层图（CLI 可统一处理）。
4. **CLI 人在回路**：`interrupt()` + `Command(resume=...)` 是官方文档明确给出的 CLI 交互模式（含 `input()` 循环示例）。
5. **流式**：`updates`（逐节点）、`messages` / `stream_events` v3（逐 token）、`custom`（`get_stream_writer()` 自定义进度）三者组合覆盖 CLI 进度渲染。
6. **版本**：1.0（2025-10）为稳定性发布，核心图 API 与执行模型不变，唯一硬性破坏变更是放弃 Python 3.9；官方承诺 2.0 前保持稳定。

真正需要写进规格的不是「能不能」，而是**三个工程坑**（详见各节「坑」小节）：

- 并行写同一 state key **必须**定义 reducer，否则冲突；
- 并行 superstep 内更新**顺序不保证**，聚合节点必须自行重排序 findings；
- `interrupt()` 恢复时**节点从头重跑**，中断前的副作用必须幂等。

---

## 1. Send API / map-reduce：fan-out / fan-in 与动态分支

### 1.1 官方惯用写法（map-reduce 教程原例）

条件边的路由函数返回 `Send` 对象列表，每个 `Send` 携带一份**独立 state** 分发到目标节点：

```python
from langgraph.types import Send

class OverallState(TypedDict):
    topic: str
    subjects: list[str]
    jokes: Annotated[list[str], operator.add]   # reducer 是 fan-in 的关键
    best_selected_joke: str

def continue_to_jokes(state: OverallState):
    # fan-out：运行时构造 Send 列表，数量由 state 决定
    return [Send("generate_joke", {"subject": s}) for s in state["subjects"]]

builder.add_conditional_edges("generate_topics", continue_to_jokes, ["generate_joke"])
builder.add_edge("generate_joke", "best_joke")   # fan-in：所有并行实例完成后才执行
```

- 出处：[Use the graph API（Python）](https://docs.langchain.com/oss/python/langgraph/use-graph-api)、[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)
- `Send(node, arg)` 两个参数：目标节点名 + 分发给该节点的 state；**该 state 可以与图的核心 state 完全不同**（官方 Send 类 docstring 原文，见[源码 types.py](https://cdn.jsdelivr.net/gh/langchain-ai/langgraph@main/libs/langgraph/langgraph/types.py)）。
- 1.2 起 `Send` 增加仅关键字参数 `timeout`（`TimeoutPolicy`），可为单个分发任务独立设置超时（出处：[changelog](https://docs.langchain.com/oss/python/releases/changelog)、源码）。

### 1.2 分支数能否动态决定？——**能，这正是 Send 的设计目的**

官方文档对 Send 的定位原文：适用于「**边的数量事先未知**」且「需要**不同版本的 State 同时存在**」的场景，典型即 map-reduce。映射到本项目：

- 「按 PR 改动内容启用专家」= 上游节点（CLI 预检/Supervisor）把 `enabled_experts` 写入 state，条件边函数据此返回对应 `Send` 列表。分支数完全由运行时数据决定。
- 两种等价形态：
  - **每个专家一个节点**：`return [Send(f"expert_{name}", {...}) for name in state["enabled_experts"]]`（`Send` 的目标节点名任意，不要求同一节点）；
  - **统一专家节点 + 参数化 payload**：`return [Send("expert", {"expert_id": e, ...}) for e in state["enabled_experts"]]`——官方教程即此形态（同一节点 N 个并行实例）。
- 不需要按实例区分输入时，也可不用 `Send`：条件边直接返回**节点名列表**（如 `["expert_a", "expert_c"]`），选中节点在下一 superstep 并行执行、共享同一份图 state（出处：[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api) Edges/分支部分、[Use the graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)）。
- 另有节点内写法：`Command(goto=[Send(...), ...])`。官方教程主推条件边写法（`Command` 的文档化场景是「同时更新状态 + 路由」），但**源码类型签名明确支持 `goto` 接收 `Send`**：`goto: Send | Sequence[Send | N] | N`，docstring 列明「`Send` object / Sequence of `Send` objects」（出处：[langgraph/types.py（jsDelivr 官方仓库镜像）](https://cdn.jsdelivr.net/gh/langchain-ai/langgraph@main/libs/langgraph/langgraph/types.py)）。若采用，结论按「支持、源码确认、教程未作为主推示例」表述。

### 1.3 fan-in 聚合的机制

1. **reducer 合并**：所有并行任务的返回值通过共享 key 的 reducer 合并进图 state。官方示例 `jokes: Annotated[list[str], operator.add]`：每个并行实例返回 `{"jokes": [x]}`，reducer 把 N 个列表拼接（出处：[Use the graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)）。
2. **聚合节点**：`generate_joke -> best_joke` 的普通边保证 `best_joke` 在**同一 superstep 的全部并行任务完成后**才执行（Pregel 超步语义：同超步并行、跨超步串行；出处：[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)）。

### 1.4 坑（必须写进规格）

| 坑 | 官方原文/机制 | 对策 |
|---|---|---|
| 并行写同一 key 无 reducer | 默认 reducer 是「覆盖」；官方警告并行 fan-out 写同 key 必须 `operator.add` 类 reducer 累加 | `findings` 必须声明 `Annotated[list, operator.add]` |
| 并行 superstep 更新顺序不保证 | 原文："updates from a parallel superstep may not be ordered consistently. If you need a consistent, predetermined ordering … write the outputs to a separate field … with a value with which to order them." | 聚合/裁决节点**必须重排序**（如按 文件+行号+严重度），不得依赖 reducer 合并顺序 |
| 同 superstep 多节点对同 key 用 `Overwrite` | 官方：同一 superstep 内只允许一个节点对同 key 使用 Overwrite，否则抛 `InvalidUpdateError` | findings 只追加、不覆盖；确需整体替换时仅由单一聚合节点做 |
| `Send` 的 arg 即该任务的全部输入 | Send 状态独立于图核心 state | 每个专家需要的上下文（diff、spec、文件清单）要么放进 `Send` arg，要么改用「节点名列表」形态让专家读共享 state |
| `Command` 与静态边混用 | 官方明确：节点返回 `Command(goto=...)` 时，已用 `add_edge` 定义的静态边**仍会执行**，两者会同时跑，行为难以推理 | 每个节点只用一种路由机制（静态边 / 条件边 / Command） |

出处统一：[Use the graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)、[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)。

---

## 2. 子图：专家封装的得失与父子图状态传递

### 2.1 两种通信模式（官方分类）

| 模式 | 适用 | 写法 |
|---|---|---|
| **节点内调用子图**（函数式/包装函数） | 父子图 **schema 不同**、无共享 key | 节点函数里 `subgraph.invoke(...)`，手工做「父→子→父」状态映射 |
| **子图作为节点** | 父子图**共享 state key** | `builder.add_node("expert_x", compiled_subgraph)`，无需包装 |

- 共享 key 模式下，子图内部可持有私有 key（如每个专家自己的 messages 历史），只把共享 key 的更新透传给父图——这正是多智能体系统（每个 agent 私有消息历史）的官方推荐结构。
- 出处：[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)。

### 2.2 持久化/checkpointer 三种配置（决定子图行为）

| 配置 | `checkpointer=` | 行为 |
|---|---|---|
| Per-invocation（**默认**） | `None`/省略 | 每次调用全新开始，但**继承父图 checkpointer**：单次调用内支持 `interrupt()` 与持久执行 |
| Per-thread | `True` | 同一 thread 上状态跨调用累积（子代理多轮记忆） |
| Stateless | `False` | 无检查点、无中断；崩溃即从头重跑 |

- 前提：**父图必须带 checkpointer 编译**，子图的中断/状态查看才生效。
- 官方选型建议：**Per-invocation 是大多数应用（含多智能体系统）的正确选择**。
- 出处：[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)。

### 2.3 对本项目有用的子图事实

- **中断传播**：无论嵌套多深，子图内的 `interrupt()` 都会传播到顶层图，CLI 端可统一用 `stream.interrupts` / `Command(resume=...)` 处理（出处：[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)）。
- **查看子图状态**：`graph.get_state(config, subgraphs=True)`；两个前提——必须启用持久化，且 LangGraph 能**静态发现**子图（作为节点添加或在节点内直接调用；经工具函数间接调用不行）（出处：[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)）。
- **命名空间**：作为节点添加的子图自动获得基于节点名的稳定命名空间；在节点函数里手工 `invoke` 子图时命名空间按**调用顺序**分配，重排会混淆状态归属（官方对策：给每个子代理包一层带唯一节点名的单节点 `StateGraph`）。
- **跨图更新**：子图节点用 `Command(graph=Command.PARENT, update=...)` 向父图写状态时，若 key 为父子共享，**父图该 key 必须定义 reducer**（出处：[Use the graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)）。

### 2.4 坑

| 坑 | 说明 | 对策 |
|---|---|---|
| Per-thread 子图 + 并行调用 | 并行调用同一 per-thread 子图会写**同一检查点命名空间**，产生冲突；纯 LangGraph 需自行禁用并行工具调用 | 专家子图用默认 **per-invocation**；每次调用独立命名空间，天然支持并行 |
| 状态可见性 | 子图状态查看依赖静态发现 + 持久化 | 专家子图统一以 `add_node` 挂载，不藏在工具函数里 |
| 检查点体积 | 长线程 checkpoint 无限累积增加延迟与存储成本 | CLI 工具短线程为主，影响小；可定期清理（出处：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)） |

### 2.5 得失结论（供 #8 票直接引用）

- **得**：复用（同一专家图在 Mentor/Gatekeeper 两图共用）、团队并行开发（只要遵守输入/输出 schema 接口）、封装内部多步流程（如「检索 Spec KB → 对照检查」两步专家）。
- **失**：状态映射代码（schema 不同时）、命名空间/可见性规则的心智负担、`Send` fan-out 与子图组合时每实例输入仍由 `Send` arg 决定。
- **建议倾向**：单步 LLM 专家用**普通节点**即可；只有内部含多步流程（如 spec 检索+对照、多轮自我校验）的专家才值得子图化（per-invocation 默认模式）。

---

## 3. State schema 惯例：TypedDict vs Pydantic 与 findings reducer

### 3.1 官方口径（Graph API overview 原文归纳）

| 方式 | 官方定位 |
|---|---|
| **TypedDict** | 文档化的**主要**方式（所有官方教程默认） |
| **dataclass** | 需要**默认值**时 |
| **Pydantic BaseModel** | 需要**递归数据验证**时；官方明示 **Pydantic 性能低于 TypedDict/dataclass** |

- ⚠️ 更高层的 LangChain `create_agent` **不支持 Pydantic state schema**（出处：[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)）。若专家用 `create_agent` 封装，state 必须 TypedDict。
- 1.1+ 的类型安全 v2 调用（`invoke(..., version="v2")`）会把结果**自动转换为声明的 Pydantic 模型 / dataclass**——「内部 TypedDict、输出侧 Pydantic 校验」是可行组合（出处：[changelog](https://docs.langchain.com/oss/python/releases/changelog)）。

### 3.2 多 schema 支持

支持 `input_schema` / `output_schema` / 整体 schema / 私有 channel（节点可声明额外 schema）。注意：私有 channel 在 `stream_mode="values"` 里**不会被隐藏**，需 `output_keys` 限制（出处：[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)）——CLI 渲染全量状态时要注意过滤。

### 3.3 reducer：多专家 findings 合并的官方写法

- 每个 key 独立 reducer，二元函数 `reducer(left=当前值, right=本次更新)`；**未指定 = 覆盖**。
- 官方示例即本项目要的形态：

```python
from operator import add
from typing import Annotated
from typing_extensions import TypedDict

class ReviewState(TypedDict):
    findings: Annotated[list[dict], add]   # 各专家返回 {"findings": [...]}，自动拼接
```

- 消息历史用专用 reducer `add_messages`（按 ID 追加/覆盖 + 自动反序列化）；`Overwrite` 类型可绕过 reducer 直接覆盖（但受并行限制，见 1.4）。
- 节点只需返回**增量**更新（`{"findings": [...]}`），不必返回整个 state。
- 出处：[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)、[Use the graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)。

### 3.4 坑

- `findings` 的元素类型：reducer 合并的是**列表**，元素校验发生在节点自身（专家节点内用 Pydantic `Finding` 模型先验证再入列表，是「行级锚定 + 三级严重度」结构化 JSON 的落点）。
- 并行合并顺序不保证（见 1.4）→ 聚合节点重排序。
- 默认覆盖语义：忘写 `Annotated[..., add]` 时，最后完成的专家会**清掉**其他人的 findings（静默 bug，规格必须显式声明 reducer）。

---

## 4. Checkpointing / interrupt：后端与 CLI 人在回路

### 4.1 后端矩阵（Checkpointers 官方页）

| 后端 | 独立 pip 包 | 定位 | 备注 |
|---|---|---|---|
| `InMemorySaver` | `langgraph-checkpoint`（随 langgraph 自带） | 测试/演示 | 进程重启即丢 |
| **`SqliteSaver` / `AsyncSqliteSaver`** | **`langgraph-checkpoint-sqlite`（单独安装）** | 官方定位「**实验和本地工作流**」 | 构造：`SqliteSaver(sqlite3.connect("checkpoint.db"), serde=...)`；支持 `EncryptedSerializer` AES 加密 |
| `PostgresSaver` / `AsyncPostgresSaver` | `langgraph-checkpoint-postgres` | **生产推荐**（LangSmith 内部使用） | 需 `setup()`；thread_id ≤ 255 字符 |
| Azure Cosmos DB | `langchain-azure-cosmosdb` | Azure 生产 | 同步+异步 |

- 使用 checkpointer 必须传 `config={"configurable": {"thread_id": ...}}`；同一 thread_id 恢复、新 thread_id 开新线程。
- 异步执行（`.ainvoke/.astream`）必须用异步版 saver（`AsyncSqliteSaver` 等）。
- 默认序列化 `JsonPlusSerializer`（ormsgpack+JSON），不支持类型需 `pickle_fallback=True`。
- 出处：[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
- **对本项目的判断**：CLI 本地工具用 `SqliteSaver` 完全落在官方定位内（「实验和本地工作流」），Mentor/Gatekeeper 的中断/恢复可落地；若日后服务化再换 PostgresSaver。

### 4.2 interrupt() 对 CLI 交互确认：**可用，且是官方主推模式**

```python
from langgraph.types import interrupt, Command

def approval_node(state):
    approved = interrupt({"question": "Approve?", "details": ...})  # 挂起，等待人工
    return {"approved": approved}
```

- 机制：调用点精确挂起 → checkpointer 存状态 → 载荷出现在 `result["__interrupt__"]`（`invoke`）或 `stream.interrupts`（`stream_events(..., version="v3")`）→ 无限期等待 → `Command(resume=值)` 恢复，**resume 值成为 `interrupt()` 的返回值**。
- 与静态断点（`interrupt_before/after`）不同，`interrupt()` 是**动态**的、可放节点内任意位置、可按条件触发；官方明确：静态断点仅用于调试，**HITL 工作流应使用 `interrupt()`**。
- 三个前提：checkpointer、thread_id、JSON 可序列化载荷。
- **官方文档直接给出 CLI 循环模式**（`get_user_input()` 换成 `input()` 即终端确认）：

```python
while True:
    stream = graph.stream_events(stream_input, config=config, version="v3")
    for message in stream.messages:          # 逐 token 展示
        for token in message.text: display(token)
    if not stream.interrupted: break         # 未中断 → 结束
    user_response = get_user_input(stream.interrupts[0].value)
    stream_input = Command(resume=user_response)
```

- 出处：[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。

### 4.3 坑（规格必须写死）

| 坑 | 官方原文/机制 | 对策 |
|---|---|---|
| **恢复时节点从头重跑** | 官方明示：节点会从头重新执行，不从断点行继续 | `interrupt()` 之前的副作用必须**幂等**（放在 interrupt 之后或拆独立节点） |
| 循环校验指数重跑 | 官方警告：节点内 `while True + interrupt()` 校验会重复执行 | 每节点单次 `interrupt()`，失败信息写入 state，用**条件边路由回**重问 |
| 裸 try/except | 会误捕获挂起异常 | 只捕获特定异常类型 |
| 恢复值按索引严格匹配 | 节点内多个 interrupt 的 resume 匹配按调用顺序 | 不在节点内重排/条件跳过 interrupt 调用 |
| 并行多中断 | 多分支同时中断需按 ID 映射恢复 | `Command(resume={i.id: value for i in stream.interrupts})` |
| 载荷序列化 | 必须可 JSON 序列化 | 传字符串/字典/数组 |

- 出处：[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)（子图中断传播）。

---

## 5. 流式输出：CLI 进度渲染的支持程度

### 5.1 两层 API（1.2 起的官方结构）

| 层 | API | 定位 |
|---|---|---|
| 底层 Streaming | `graph.stream(..., stream_mode=...)` | Pregel 原始事件，模式：`values / updates / messages / custom / checkpoints / tasks / debug`，可传列表组合；`version="v2"`（≥1.1）输出统一 `StreamPart`（`{type, ns, data}`），可编辑器类型收窄 |
| 上层 Event streaming（1.2+，beta） | `graph.stream_events(..., version="v3")` | 类型化投影 API，官方**推荐新应用使用** |

- 出处：[Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)。

### 5.2 CLI 进度渲染逐项对照

| 需求 | 支持度 | 写法 |
|---|---|---|
| **逐节点进度**（哪个专家在跑） | ✅ | `stream_mode="updates"`：每节点完成即出增量（同一 superstep 多个更新分开流式）；或 `tasks` 模式（任务开始/结束，需 checkpointer） |
| **逐 token**（专家输出实时渲染） | ✅ | `stream_mode="messages"`：`(LLM token, metadata)` 二元组；**节点内用 `.invoke()` 也会发出**；`version="v3"` 的 `stream.messages` 提供 `text / reasoning / tool_calls / usage` 类型化子投影 |
| **自定义进度**（如「已检索 100/100 条」） | ✅ | 节点/工具内 `get_stream_writer()` 发任意键值，`stream_mode="custom"` 消费（可与其他模式组合） |
| 过滤 | ✅ | 按 `metadata["langgraph_node"]`（节点）或模型 `tags`（调用）过滤；`nostream` 标签排除内部结构化输出调用的 token |
| 子图/专家内部 token | ✅ 有条件 | 需 `subgraphs=True`（streaming）或 `stream.subgraphs` 投影（v3） |
| 消费多个投影 | ✅ | v3 多消费者并发（读 messages 不消耗 values）；同步用 `stream.interleave("values", "messages", "subgraphs")` |

- 出处：[Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)。

### 5.3 坑

- **非 LangChain 集成的 LLM 拿不到 `messages` 模式**：需改用 `custom` 模式手动转发 token（官方给出演示写法：节点里循环自定义流式客户端、逐 chunk `writer({...})`）。
- **异步 + Python < 3.11 时 `get_stream_writer()` 不可用**：改在函数签名声明 `writer: StreamWriter` 由 LangGraph 注入（异步节点还应显式传 `config`）。
- Event streaming v3 仍是 **beta**；底层 `stream_mode` 体系稳定。CLI 工具建议：进度/自定义事件用 `stream_mode=["updates","custom"]`（稳定层），逐 token 渲染可选 v3 或 `messages`。
- `updates` 只是底层通道名，**v3 没有对应 `stream.updates` 投影**（需要增量更新时直接迭代原始事件或退回底层 streaming）。

---

## 6. 版本现状与 1.x 演进（面试必问部分）

### 6.1 当前状态（PyPI 一手数据）

- **最新稳定版：langgraph 1.2.11**（2026-08-11 发布）；Development Status: `5 - Production/Stable`；`Python >= 3.10`。
- 时间线（均为 PyPI 实际发布日期）：

| 版本 | 日期 | 要点 |
|---|---|---|
| 0.6.x | 2025-07 ~ 2025-10 | 0.x 末代（0.6.0 引入 `config_schema`→`context_schema` 弃用） |
| **1.0.0** | 2025-10-17 | 与 LangChain 1.0 同期；稳定性发布 |
| 1.1.0 | 2026-03-10 | 类型安全 v2 调用/流式（opt-in） |
| 1.2.0 | 2026-05-12 | 节点超时/错误处理/优雅停机/DeltaChannel/`stream_events` v3 |
| **1.2.11** | 2026-08-11 | 当前稳定版 |

- 被撤回（yanked）版本：`1.2.3`（merging 策略回归）、`1.1.7`（自定义回调处理器 bug）——升级时避开即可。
- 出处：[PyPI langgraph](https://pypi.org/project/langgraph/)。

### 6.2 1.0 的变更口径（官方）

- **「v1 是以稳定性为核心的发布」**：图原语（state/nodes/edges）与执行模型**完全不变**；持久化、checkpointer、流式、HITL 仍是一等公民（出处：[What's new in LangGraph v1](https://docs.langchain.com/oss/python/releases/langgraph-v1)）。
- 迁移指南结论原文：**「LangGraph v1 与之前的版本基本向后兼容」**，破坏性变更仅一条——**放弃 Python 3.9**（要求 ≥3.10）（出处：[LangGraph v1 migration guide](https://docs.langchain.com/oss/python/migrate/langgraph-v1)）。
- 弃用清单（同页）：`create_react_agent` → `langchain.agents.create_agent`（参数 `prompt` → `system_prompt`）；`AgentState` 系列、`HumanInterruptConfig`、`ActionRequest`、`HumanInterrupt`（→ `InterruptOnConfig` / `HITLRequest`）、`ValidationNode`、`MessageGraph`。
- 官方博客定调：首个「durable agent framework」领域的稳定大版本，2.0 前保持稳定承诺（出处：[LangChain and LangGraph Agent Frameworks Reach v1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0)，2025-10-22）。

### 6.3 1.1 / 1.2 新增（均无破坏性变更）

- **1.1（2026-03-10）**：`invoke/stream` `version="v2"`（统一 `StreamPart`、`GraphOutput.value/.interrupts`、Pydantic/dataclass 自动转换）；修复带 interrupts/subgraphs 的时间旅行。废弃：`GraphOutput` 的 dict 风格访问。
- **1.2（2026-05-12）**：`add_node(timeout=...)`（⚠️ 仅异步节点）；`add_node(error_handler=...)`（⚠️ 仅 Python）；优雅停机 `RunControl.request_drain()` + `GraphDrained`（当前 superstep 完成后停并存可恢复 checkpoint）；`DeltaChannel`（beta，增量存储长列表 channel）；`stream_events(version="v3")`（beta）。
- 出处：[Changelog](https://docs.langchain.com/oss/python/releases/changelog)。

### 6.4 相关弃用

- `config_schema` 自 v0.6.0 弃用、将在 v2.0.0 移除，改用 `context_schema`（出处：[LangGraph API Reference — Graphs](https://reference.langchain.com/python/langgraph/graphs/)，经 reference.langchain.org.cn 镜像核对）。

### 6.5 Supervisor 库现状（对拓扑票的补充事实）

- 官方 `langgraph-supervisor` 库**不再作为主推实现**：官方现推荐**直接用工具调用实现 supervisor 模式**（对上下文工程控制更灵活），该库转为帮助存量用户向 LangChain 1.0 迁移的维护性质（出处：[langgraph_supervisor API 参考](https://reference.langchain.com/python/langgraph-supervisor)、[langgraph-supervisor-py 仓库](https://github.com/langchain-ai/langgraph-supervisor-py)）。
- 纯 LangGraph 层面的 Supervisor = 一个 LLM 路由节点 + 条件边/`Command`；`create_supervisor()` 返回 `StateGraph` 需 `.compile()`，支持 checkpointer/store/多级层级。
- **对本项目的含义**：自建 Supervisor（规则/LLM 路由节点）不依赖任何将弃用的库；若想要高层封装，走 `create_agent` + handoff 工具而非 `create_react_agent`/`langgraph-supervisor`。

---

## 7. 对专家团票（#8）的直接输入：能力 → 设计映射

| 设计问题 | 事实依据（本报告） | 可行方案 |
|---|---|---|
| Supervisor 如何派发专家 | §1.2：条件边返回 `Send` 列表，分支数运行时决定 | 预检节点产出 `enabled_experts`（按 PR 改动的语言/文件类型、Spec KB 是否为空）→ 条件边 `[Send(f"expert_{e}", {...})]`；**Spec KB 为空 → 直接不启用规范专家**，即「可留空则跳过」的原生实现 |
| N 个专家的 findings 如何汇合 | §1.3/§3.3：`findings: Annotated[list, operator.add]` + 聚合节点 | 专家返回 `{"findings": [...]}`；聚合/裁决节点在全部专家完成后执行 |
| 聚合裁决（去重/排序/严重度仲裁） | §1.4：并行合并顺序不保证 | 聚合节点内按（文件, 行号, 严重度, 规则 ID）重排序 + 去重；这是**必需步骤**而非优化 |
| 专家形态 | §2.5：普通节点 vs 子图 | 单步 LLM 专家 = 普通节点；含内部多步（Spec 检索+对照）= per-invocation 子图 |
| Gatekeeper 模式人工确认 | §4.2：`interrupt()` + `Command(resume=...)`，官方 CLI 循环模式 | 裁决后 `interrupt()` 展示 findings 摘要 → CLI `input()` 确认 → `Command(resume=True/False)` 路由到「生成修复建议 / 终止」 |
| Mentor 模式渐进引导 | §5：`messages` 逐 token + `custom` 进度 | `stream_mode=["updates","custom","messages"]` 或 `stream_events(version="v3")` |
| 断点续跑 / 幂等 | §4.3：恢复重跑节点 | 专家节点设计成幂等（findings 全量重算）；`interrupt()` 尽量放在节点开头附近 |
| 单 Agent 基线对照 | §6.5：`create_agent`（LangChain 1.x 推荐入口） | 同一 `Finding` 结构化输出 schema，单节点图跑全流程，作为专家团的对照实验 |
| 状态校验 | §3.1：TypedDict 首选 + 输出侧 Pydantic | state 用 TypedDict；`Finding` 用 Pydantic 模型在节点内验证后入列表；输出用 v2 invoke 自动转换 |

---

## 8. 能力-模式对照表（结票主表）

> 结论分三档：**支持**（官方文档/源码直接支持）/ **有坑**（支持但需按对策规避）/ **不支持**（官方明确不支持或已弃用）。

| # | 能力 / 模式 | 结论 | 说明（含坑与对策） | 出处 |
|---|---|---|---|---|
| 1 | Send fan-out：条件边返回 `Send` 列表，同一节点 N 个并行实例 | **支持** | 官方 map-reduce 教程原生形态；`Send(node, arg)` 的 arg 可 ≠ 图核心 state | [use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) |
| 2 | 分支数运行时动态决定（按 PR 启用专家） | **支持** | Send 列表在运行时构造；「边的数量事先未知」正是 Send 的设计场景 | [graph-api](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| 3 | fan-out 到**不同**专家节点 | **支持** | Send 目标节点名任意；或条件边返回节点名列表（共享 state 形态） | [use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) |
| 4 | 节点返回 `Command(goto=[Send(...)])` | **支持**（源码确认；教程主推条件边写法） | `goto: Send \| Sequence[Send \| N] \| N`；勿与静态边混用 | [types.py 源码](https://cdn.jsdelivr.net/gh/langchain-ai/langgraph@main/libs/langgraph/langgraph/types.py) |
| 5 | fan-in：reducer 合并 + 聚合节点 | **支持** | `Annotated[list, operator.add]`；聚合节点在全部并行任务完成后执行（Pregel 超步） | [use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) |
| 6 | 并行专家写同一 key（findings） | **有坑** | 必须声明 reducer（默认覆盖会静默丢数据）；聚合节点必须重排序（并行更新顺序不保证） | [use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) |
| 7 | 同 superstep 并行 Overwrite 同 key | **有坑** | 抛 `InvalidUpdateError`；整体替换只允许单一节点执行 | [use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) |
| 8 | 每分发任务独立超时 | **支持**（1.2+） | `Send(..., timeout=...)`（TimeoutPolicy） | [changelog](https://docs.langchain.com/oss/python/releases/changelog) |
| 9 | 专家封装为子图（共享 key，`add_node` 直挂） | **支持** | 编译子图直接 `add_node`；内部可持私有 key | [use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) |
| 10 | 父子图不同 schema 的状态传递 | **支持** | 包装函数做父↔子映射；`Command(graph=Command.PARENT)` 回写父图（共享 key 需父图 reducer） | [use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)、[use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) |
| 11 | 子图内 `interrupt()` 传播到顶层 | **支持** | CLI 统一处理；前提：父图带 checkpointer + 子图非 stateless | [use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) |
| 12 | per-thread 子图 + 并行调用 | **有坑** | 同命名空间检查点冲突；专家子图应使用默认 per-invocation | [use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) |
| 13 | 查看子图内部状态 | **有坑** | 需持久化 + 静态发现（勿把子图藏在工具函数里） | [use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) |
| 14 | TypedDict state | **支持**（官方首选） | 教程默认方式 | [graph-api](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| 15 | Pydantic state | **有坑** | 可用但性能更低；`create_agent` **不支持** Pydantic state；输出侧可用 v2 invoke 自动转换 | [graph-api](https://docs.langchain.com/oss/python/langgraph/graph-api)、[changelog](https://docs.langchain.com/oss/python/releases/changelog) |
| 16 | `Annotated[list, operator.add]` 合并 findings | **支持** | 官方示例原样；元素结构校验由专家节点内 Pydantic 完成 | [graph-api](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| 17 | `SqliteSaver` 本地持久化 | **支持**（定位=实验/本地） | 单独安装 `langgraph-checkpoint-sqlite`；异步需 `AsyncSqliteSaver`；生产换 PostgresSaver | [checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) |
| 18 | `interrupt()` + `Command(resume=...)` CLI 确认 | **支持** | 官方即给出 CLI 循环模式；需 checkpointer + thread_id | [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) |
| 19 | interrupt 恢复从断点行继续 | **不支持**（语义即从头重跑节点） | 官方明示节点从头重执行 → 中断前副作用必须幂等 | [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) |
| 20 | 静态断点做 HITL | **不推荐** | 官方定位仅调试用；HITL 用 `interrupt()` | [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) |
| 21 | 逐节点进度流式 | **支持** | `stream_mode="updates"`（v3 无 updates 投影，用底层）；`tasks` 模式含开始/结束 | [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) |
| 22 | 逐 token 流式 | **支持** | `messages` 模式（`.invoke()` 也发出）；或 v3 `stream.messages`（text/reasoning/tool_calls）；子图需 `subgraphs=True` | [streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[event-streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming) |
| 23 | 自定义进度事件 | **支持** | `get_stream_writer()` + `custom` 模式；async+Py<3.11 需 `writer: StreamWriter` 参数注入 | [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) |
| 24 | 非 LangChain LLM 逐 token | **有坑** | `messages` 模式不可用 → `custom` 模式手动转发 | [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) |
| 25 | Python 3.9 | **不支持** | 1.0 起要求 ≥3.10（唯一硬性破坏变更） | [migration](https://docs.langchain.com/oss/python/migrate/langgraph-v1) |
| 26 | `create_react_agent` | **已弃用** | → `langchain.agents.create_agent`（`prompt`→`system_prompt`） | [migration](https://docs.langchain.com/oss/python/migrate/langgraph-v1) |
| 27 | `langgraph-supervisor` 库 | **有坑**（不再主推） | 官方推荐直接工具调用实现 supervisor；库转为 1.0 迁移维护 | [reference](https://reference.langchain.com/python/langgraph-supervisor)、[repo](https://github.com/langchain-ai/langgraph-supervisor-py) |
| 28 | 1.x 破坏性变更 | **无**（1.1/1.2 均向后兼容） | 1.0 唯一破坏=弃 Python 3.9；2.0 前稳定承诺 | [langgraph-v1](https://docs.langchain.com/oss/python/releases/langgraph-v1)、[blog](https://www.langchain.com/blog/langchain-langgraph-1dot0) |
| 29 | 节点超时 / 错误处理器 / 优雅停机 | **支持**（1.2，各有限制） | timeout 仅异步节点；error_handler 仅 Python；drain 在 superstep 边界停 | [changelog](https://docs.langchain.com/oss/python/releases/changelog) |
| 30 | `config_schema` | **已弃用**（v0.6 起，v2.0 移除） | 改用 `context_schema` | [reference Graphs](https://reference.langchain.com/python/langgraph/graphs/) |

---

## 9. 来源清单（全部一手）

**官方文档（docs.langchain.com）**
1. LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
2. Graph API overview（State schema/reducer/Send/Command）: https://docs.langchain.com/oss/python/langgraph/graph-api
3. Use the graph API（map-reduce/并行坑）: https://docs.langchain.com/oss/python/langgraph/use-graph-api
4. Subgraphs: https://docs.langchain.com/oss/python/langgraph/use-subgraphs
5. Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
6. Checkpointers: https://docs.langchain.com/oss/python/langgraph/checkpointers
7. Interrupts（HITL/CLI 模式）: https://docs.langchain.com/oss/python/langgraph/interrupts
8. Streaming: https://docs.langchain.com/oss/python/langgraph/streaming
9. Event streaming（v3 投影 API）: https://docs.langchain.com/oss/python/langgraph/event-streaming
10. What's new in LangGraph v1: https://docs.langchain.com/oss/python/releases/langgraph-v1
11. LangGraph v1 migration guide: https://docs.langchain.com/oss/python/migrate/langgraph-v1
12. Python releases changelog（1.1/1.2）: https://docs.langchain.com/oss/python/releases/changelog

**官方参考 / 源码 / 发布记录**
13. langgraph-supervisor API 参考: https://reference.langchain.com/python/langgraph-supervisor
14. LangGraph API Reference — Graphs（config_schema 弃用）: https://reference.langchain.com/python/langgraph/graphs/
15. langgraph/types.py（Send/Command 源码，官方仓库 jsDelivr 镜像）: https://cdn.jsdelivr.net/gh/langchain-ai/langgraph@main/libs/langgraph/langgraph/types.py
16. langgraph-supervisor-py 仓库: https://github.com/langchain-ai/langgraph-supervisor-py
17. PyPI langgraph（版本史，1.2.11 / 2026-08-11）: https://pypi.org/project/langgraph/
18. LangChain and LangGraph Agent Frameworks Reach v1.0（官方博客，2025-10-22）: https://www.langchain.com/blog/langchain-langgraph-1dot0

> 备注：`reference.langchain.org.cn` 为 reference.langchain.com 的第三方镜像，仅用于核对 #14 的弃用条目原文；其余结论均直接来自 docs.langchain.com / 官方源码 / PyPI。
