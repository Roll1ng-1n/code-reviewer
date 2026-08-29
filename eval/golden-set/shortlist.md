# Golden set 候选 shortlist（人工判定用）

勾选后进入标注。context_hint/type_hint 仅为分层提示，以你判断为准。

- [ ] **S1** [langchain-ai/langgraph#8617](https://github.com/langchain-ai/langgraph/pull/8617) fix(checkpoint): widen `Store` `put` value type to `Mapping[str, Any]`
  - 规模 +35/-17，8 文件；分层提示 T3/architecture
  - 评审摘录：<!-- open-swe-review-comment {"id":"f_5202641240","file_path":"libs/checkpoint/langgraph/store/base/__init__.py","start_line":476,"end_line":479,"side":"RIGHT"}
  - 修复证据：fix(checkpoint-postgres,checkpoint-sqlite): normalize Mapping values to dict before JSON serialization；Merge branch 'main' into fix/store-put-value-mapping

- [ ] **S2** [microsoft/autogen#7054](https://github.com/microsoft/autogen/pull/7054) Add missing reasoning_effort parameter support for OpenAI GPT-5 models
  - 规模 +118/-0，2 文件；分层提示 T2/logic
  - 评审摘录：  ```suggestion      reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]]  ```
  - 修复证据：Merge branch 'main' into copilot/fix-9af7443e-3790-4413-a988-25b41c64714d；Fix pyright errors in reasoning_effort tests by adding ignore comments for private usage

- [ ] **S3** [langchain-ai/langgraph#8569](https://github.com/langchain-ai/langgraph/pull/8569) fix(langgraph): detect subgraphs from bytecode instead of source
  - 规模 +358/-142，2 文件；分层提示 T2/logic
  - 评审摘录：This holds when the captured value *is* the graph, but not when the graph sits one attribute off it. The closure root survives; the `LOAD_ATTR` chain doesn't, a
  - 修复证据：Merge branch 'main' into fix/subgraph-detection-from-bytecode

- [ ] **S4** [langchain-ai/langgraph#8526](https://github.com/langchain-ai/langgraph/pull/8526) fix(checkpoint): collect writes at plain-value seed in delta channel history
  - 规模 +241/-44，4 文件；分层提示 T3/logic
  - 评审摘录：let's do imports at the top level of the file not within the function  side note, we should have a lint rule that enforces this, but doesn't need to be added in
  - 修复证据：test(checkpoint): address review feedback

- [ ] **S5** [langchain-ai/langgraph#8598](https://github.com/langchain-ai/langgraph/pull/8598) feat(sdk-py): add decrypt replacement result
  - 规模 +88/-11，5 文件；分层提示 T3/logic
  - 评审摘录：<!-- open-swe-review-comment {"id":"f_3f966a0883","file_path":"libs/sdk-py/langgraph_sdk/encryption/types.py","start_line":80,"end_line":82,"side":"RIGHT"} --> 
  - 修复证据：fix(sdk): preserve decrypt handler types

- [ ] **S6** [microsoft/autogen#7521](https://github.com/microsoft/autogen/pull/7521) Update maintanence mode banner in readme
  - 规模 +19/-10，1 文件；分层提示 T2/style
  - 评审摘录：Typo - double `M` in Microsoft, `AF` suffix in Framework and `in` instead of `is`.
  - 修复证据：Fixing Typo

- [ ] **S7** [openai/openai-python#3747](https://github.com/openai/openai-python/pull/3747) chore(deps-dev): bump mypy from 1.17 to 2.3.1
  - 规模 +282/-43，3 文件；分层提示 T3/style
  - 评审摘录：**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Restore the release marker after regenerating the lockfile**  On every C
  - 修复证据：fix: preserve mypy exclusions and release marker

- [ ] **S8** [pydantic/pydantic#13717](https://github.com/pydantic/pydantic/pull/13717) Use the field name in validation JSON schemas when `validate_by_alias` is `False`
  - 规模 +34/-1，2 文件；分层提示 T2/logic
  - 评审摘录：```suggestion             alias = name if not self._config.validate_by_alias else field.get('validation_alias', name) ```
  - 修复证据：Apply suggestions from code review

## 干净 PR 候选（对照组，测假阳性）

- [ ] [langchain-ai/langgraph#8595](https://github.com/langchain-ai/langgraph/pull/8595) release(langgraph): 1.2.11 (+5/-5)
- [ ] [langchain-ai/langgraph#8565](https://github.com/langchain-ai/langgraph/pull/8565) release(checkpoint-postgres): 3.1.2 (+4/-4)
- [ ] [pydantic/pydantic#8425](https://github.com/pydantic/pydantic/pull/8425) Fix pydantic-core version in history (+1/-1)
- [ ] [pydantic/pydantic#8424](https://github.com/pydantic/pydantic/pull/8424) Fix history (+0/-1)
- [ ] [crewAIInc/crewAI#6222](https://github.com/crewAIInc/crewAI/pull/6222) feat: bump versions to 1.14.8a1 (+0/-0)
- [ ] [crewAIInc/crewAI#5747](https://github.com/crewAIInc/crewAI/pull/5747) fix(ci): make nightly publish idempotent and serialized (+6/-4)
- [ ] [run-llama/llama_index#22843](https://github.com/run-llama/llama_index/pull/22843) fix(tools/mcp): correct streamable-http tuple unpack for mcp 2.0 (#22655) (+53/-2)