# 审查正确性与评估隔离修复

2026-10-04，对应 Issue #24–#35。所有新增回归都使用 ScriptedProvider 或 Mock，无真实模型请求。

| Issue | 修复与可观测行为 | 回归文件 |
| --- | --- | --- |
| #24、#25 | 复核候选独立编号 `C001..`，按 id 返回原对象；未知、重复、缺失 id、改写字段或解析失败均整批保留并告警 | `tests/test_safety_regressions.py` |
| #26、#27 | 每次新交互运行独立身份；当前 interrupt/resume 复用身份；完整校验 token 和 `1..N`，非法输入保留全部 | `tests/test_safety_regressions.py`、`tests/test_interactive.py` |
| #28 | 入口与依赖读取前 resolve 并检查仓库边界；越界 import、diff 路径、跨盘路径和外部链接不进入模型输入；`context_stats.outside_paths_skipped` 可观测 | `tests/test_context_regressions.py` |
| #29 | 组内最高严重度取最小 rank；仅压回真实升级，原样返回和合法降级保持 | `tests/test_safety_regressions.py` |
| #30 | 先完成同名覆盖，再对最终文档内容去重；Report 的数量和 hash 与实际库一致 | `tests/test_safety_regressions.py`、`tests/test_spec_kb.py` |
| #31 | 配置、diff、描述的读取及 UTF-8 错误返回 64，stderr 标明文件，无 Report，不构造模型 | `tests/test_safety_regressions.py` |
| #32 | 执行、Report 校验、超时或评分失败中止整轮；输出案例、臂及重复序号；不覆盖旧结果 | `tests/test_product_evaluation.py` |
| #33 | 两臂共享启用维度、专家 charter、Spec KB 和 style 护栏；metadata 记录 `arm`、`enabled_experts` | `tests/test_baseline_regressions.py` |
| #34 | 隔离 cwd、明确模型配置及显式空 `--repo`；校验实际模型和配方；分别记录被评模型、judge、配置与配方 | `tests/test_product_evaluation.py` |
| #35 | check 固定提交 SHA，diff、提交描述与源码视图均来自此提交；只读 Git 对象生成临时快照；precheck 和 Replay 继续用工作快照 | `tests/test_context_regressions.py`、`tests/test_git_input.py` |

Report 保持 schema-v1、五段结构、确定性排序和 `F001..` 连续编号。metadata 允许增加键：Git check 的 `source_commit` 为固定被审 SHA，既有 `head_ref` 仍为 `HEAD`；Replay 的两个 ref 仍为 null。临时 Git 快照只物化普通文件，不展开符号链接或子模块，不 checkout/reset 工作区。

复核协议为 `{"findings": [{"id": "C001"}]}`；模型可返回候选原字段，但任何改写都会触发整批保留。空返回继续采用保守保留策略，警告与实际动作一致。

产品评估使用 `python eval/golden-set/compare_product.py --runs 3 --model deepseek-chat`。成功结果保存 `evaluated_model`、`judge_model`、`configuration`、`recipe`；当前 judge 仍为 `deepseek-chat`，这不意味着已取得新模型效果结论。此次只验证行为与实验输入，不重新请求真实模型生成评估分数。

运行行为测试：`python -m pytest -q -p no:cacheprovider --basetemp <仓库外的临时目录>`。Windows 如默认临时目录不可读，可同时将 `TEMP`、`TMP` 指向可写的仓库外目录。回归包含隔离 Git 仓库、真实 CLI Replay、内存/SQLite checkpoint，以及 Windows junction 路径边界。
