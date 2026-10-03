# 离线预检

`reviewer precheck` 默认使用进程内 llama.cpp 推理，只读取你指定的本地 GGUF 文件。它不会自动下载权重，不使用 localhost HTTP 服务，也不会因本地模型不可用而回退到 DeepSeek。`check` 继续使用根 `model` 配置，默认 DeepSeek。

## 准备本地模型

先在有条件安装依赖的环境中安装本地推理扩展：

```shell
python -m pip install -e ".[local]"
```

自行准备支持 chat completion 的 GGUF 代码模型，在启动目录的 `.reviewer.yaml` 中配置：

```yaml
precheck:
  model:
    provider: llama_cpp
    name: local-code
    path: models/code.gguf

# check 或显式允许联网的 precheck 使用此配置。
model:
  provider: deepseek
  name: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY
```

`path` 相对 `.reviewer.yaml` 所在目录解析，也可使用绝对路径；`--repo` 不改变模型路径的基准。模型权重应放在仓库之外或加入 Git 忽略规则。`name` 是写入 Report 元数据的模型标识，请使用能识别实际模型版本的名称。

```shell
reviewer precheck --description "说明改动意图"
reviewer precheck --diff-file change.diff --repo SNAPSHOT --json
reviewer precheck --arm baseline --diff-file change.diff --json
```

两种审查臂都使用同一本地 provider、提示词和 Report 契约。默认 CPU 推理、上下文窗口 8192 tokens、temperature=0、JSON 输出；并行专家对同一本地引擎的请求串行执行，每次审查结束释放引擎。所选 GGUF 模型应包含合适的 chat template，并能遵守 JSON 指令；超过上下文窗口或生成失败返回运行错误，不静默截掉审查输入。

非空 diff 缺少 `precheck.model.path`、模型文件不存在、缺少本地扩展或无法加载 GGUF 时，退出 `64`，不产生 Report。本地推理运行失败退出 `70`。空 diff 仍直接产生无改动 Report，不需要模型或 API key。

## 显式允许远程预检

仅在本次调用传入 `--allow-network` 时，预检才使用根 `model` 配置：

```shell
reviewer precheck --allow-network --description "说明改动意图"
```

根 `model` 为 DeepSeek 时，需要 `model.api_key_env` 指定的环境变量，并会发送 diff、描述及组装的代码和规范上下文。配置文件不能将默认预检切换到远程 provider；`precheck.model.provider` 只接受 `llama_cpp`。单独设置 API key、`--mode` 或 `--arm` 不会开启远程预检。

## 验证边界

`tests/test_offline_precheck.py` 从真实 CLI/provider 工厂运行，使用替代本地引擎验证本地加载、两臂接线、串行调用、资源释放、缺配置/依赖/文件、推理失败、空 diff 与显式联网分支。测试阻断 HTTP 请求，不使用真实权重，不证明特定 GGUF 模型的审查质量。更换模型后应通过真实配对评估验证效果。
