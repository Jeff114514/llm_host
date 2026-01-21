## 概览

`config` 目录用于集中管理 **应用运行所需的所有配置文件**。  
本文件只说明各配置文件的结构与字段含义，如何使用这些配置启动服务请参考：

- `docs/QUICKSTART.md`
- `docs/START_GUIDE.md`

## 文件结构

```text
config/
├── config.yaml          # 应用主配置
├── api_keys.json        # API Key 列表
├── vllm_start_cmd.txt   # vLLM 启动命令模板（可选）
└── sglang_start_cmd.txt # sglang 启动命令模板（可选）
```

## `config.yaml` – 应用主配置

该文件会被 `app.config_manager` 解析为 `AppConfig` 对象，是整个服务的核心配置入口。

### 主要配置项

- **vLLM 服务配置**
  - `vllm_host`: vLLM 服务监听地址（默认 `localhost`）
  - `vllm_port`: vLLM 服务端口（默认 `8002`）
- **sglang 服务配置**
  - `sglang_host`: sglang 服务监听地址（默认 `localhost`）
  - `sglang_port`: sglang 服务端口（默认 `8003`）
- **FastAPI 服务配置**
  - `fastapi_host`: FastAPI 监听地址（默认 `0.0.0.0`）
  - `fastapi_port`: FastAPI 监听端口（默认 `8001`）
- **API Key 文件路径**
  - `api_keys_file`: API key 列表文件路径（默认 `config/api_keys.json`）
- **请求限制（`rate_limit`）**
  - `qps`: 每秒请求数上限，`null` 表示不启用 QPS 限制
  - `concurrent`: 全局与每 Key 并发连接上限，`null` 表示不启用并发限制
  - `tokens_per_minute`: 每分钟 Token 限制，`null` 表示不启用
- **日志级别**
  - `log_level`: 应用日志级别（`DEBUG` / `INFO` / `WARNING` / `ERROR`）
- **模型路由（`model_backend_mapping`）**
  - 用于配置 **模型名 -> 后端类型**（`vllm` / `sglang`）
  - 手动映射优先级最高；若为空则会从后端 `/v1/models` 自动发现并聚合
- **vLLM 启动与 LoRA 配置（`vllm` 节）**
  - `auto_start`: FastAPI 启动时是否自动拉起 vLLM
  - `launch_mode`: 启动模式（`python_api` / `cli`）
  - `start_cmd_file`: 默认读取 vLLM 启动命令的文件路径
  - `start_cmd`: 可选的启动命令字符串（若设置，优先于 `start_cmd_file`）
  - `log_dir` / `log_file`: vLLM 日志目录与日志文件
  - `pid_dir` / `pid_file`: vLLM PID 目录与 PID 文件
  - `python_launcher`: Python 启动器相关设置（conda 环境名、env 文件等）
  - `extra_env`: 启动 vLLM 进程时附加的环境变量
  - `lora`: LoRA 相关配置（是否启用、最大 LoRA 数量、预加载列表、运行时 resolver 等）
- **sglang 启动配置（`sglang` 节）**
  - `auto_start`: FastAPI 启动时是否自动拉起 sglang
  - `launch_mode`: 启动模式（`python_api` / `cli`）
  - `start_cmd_file`: 默认读取 sglang 启动命令的文件路径
  - `start_cmd`: 可选的启动命令字符串（若设置，优先于 `start_cmd_file`）
  - `log_dir` / `log_file`: sglang 日志目录与日志文件
  - `pid_dir` / `pid_file`: sglang PID 目录与 PID 文件
  - `python_launcher`: Python 启动器相关设置（conda 环境名、env 文件等）
  - `extra_env`: 启动 sglang 进程时附加的环境变量

> 这些字段在代码中对应 `AppConfig` / `VLLMConfig` / `SGLangConfig` 等 Pydantic 模型，具体使用逻辑可参考 `app/models.py`、`app/vllm_manager.py`、`app/sglang_manager.py`。

### 环境变量覆盖

- `CONFIG_FILE` 环境变量可用来指定自定义的配置文件路径：

```bash
export CONFIG_FILE=/path/to/your_config.yaml
```

在这种情况下，`app.config_manager.load_config()` 会优先加载该文件。

## `api_keys.json` – API Key 列表

该文件被 `app.auth.APIKeyAuth` 加载为内存中的 `APIKeyInfo` 列表，用于请求认证与 admin 权限判定。

### 基本结构

```json
{
  "keys": [
    {
      "key": "sk-your-key",
      "user": "user1",
      "quota": 10000,
      "enabled": true
    }
  ]
}
```

### 字段含义

- `key`: 实际的 API key 字符串（必填）
- `user`: 逻辑用户名或标识（可选，用于日志与监控；当值为 `"admin"` 时可访问 `/admin/*`）
- `quota`: 预留的配额字段（当前代码未强制使用，可用于后续扩展）
- `enabled`: 是否启用该 key

### 自动创建

- 文件不存在时，`APIKeyAuth` 会自动创建一个包含默认 key 的文件（`sk-default-key-change-me`），建议启动后立即修改或禁用。

## `vllm_start_cmd.txt` – vLLM 启动命令模板（可选）

用于存放一条完整的 vLLM 启动命令字符串，例如：

```text
python -m vllm.entrypoints.openai.api_server --tokenizer-mode auto --model /path/to/model --dtype bfloat16 -tp 6 --disable-log-requests --port 8002 --gpu 0.9 --max-num-seqs 512 --served-model-name MyModel --enable-prefix-caching
```

### 使用方式

在运行时：

- `app.vllm_manager.VLLMManager` 会按以下优先级解析启动命令：
  1. `VLLMConfig.start_cmd`（`config.yaml` 中显式配置）
  2. `VLLMConfig.start_cmd_file` 所指向的文件（默认即本文件）
- `scripts/start.sh` 也会读取该文件（或环境变量）构造启动命令。

该文件本身不包含逻辑，仅提供一个可外部编辑的命令模板，方便在不改代码的前提下调整 vLLM 启动参数。

## `sglang_start_cmd.txt` – sglang 启动命令模板（可选）

用于存放一条完整的 sglang 启动命令字符串，例如：

```text
python -m sglang.launch_server --host 0.0.0.0 --port 8003 --model-path /path/to/model --served-model-name MyModel
```

在运行时：

- `app.sglang_manager.SGLangManager` 会按以下优先级解析启动命令：
  1. `SGLangConfig.start_cmd`（`config.yaml` 中显式配置）
  2. `SGLangConfig.start_cmd_file` 所指向的文件（默认即本文件）

