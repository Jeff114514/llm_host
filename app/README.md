## 概览

`app` 包是整个 vLLM Proxy 的核心业务层，负责：

- **配置加载**：从 `config/config.yaml` 解析为强类型 `AppConfig`
- **认证与权限**：基于 API Key 的请求身份校验与 admin 判定
- **限流与并发控制**：QPS、全局并发、按 Key 并发、按分钟 Token 用量
- **监控与日志**：Prometheus 指标、结构化 JSON 日志、Token 使用记录
- **vLLM 请求转发**：兼容 OpenAI API 的代理层（含流式/非流式）
- **vLLM 进程管理**：按配置自动启动/停止 vLLM，支持 LoRA 配置与动态加载
- **日志轮转**：按大小 & 按天轮转与清理日志文件

## 模块结构

```text
app/
├── __init__.py          # 包初始化
├── main.py              # FastAPI 主应用与生命周期管理
├── routes.py            # 所有 HTTP 路由与业务入口
├── auth.py              # API Key 认证与 admin 权限判断
├── limiter.py           # QPS / 并发 / Token 限流
├── monitoring.py        # Prometheus 指标与结构化日志
├── models.py            # 所有配置与领域模型（Pydantic）
├── config_manager.py    # 配置加载与全局单例
├── vllm_client.py       # 调用 vLLM OpenAI 兼容接口的 HTTP 客户端
├── vllm_manager.py      # vLLM 进程启动 / 健康检查 / 停止
└── log_manager.py       # 日志文件轮转、清理与统计
```

## 核心流程

### 1. 应用启动与生命周期（`main.py`）

- **配置初始化**
  - 通过 `init_config()` 读取 `AppConfig`（默认 `config/config.yaml`，可由环境变量 `CONFIG_FILE` 覆盖）
  - 创建全局的 `APIKeyAuth` 和 `RequestLimiter` 实例
- **FastAPI 应用装配**
  - 创建 `FastAPI` 对象，设置标题、描述、版本
  - 挂载 `CORSMiddleware`（全开放 CORS）
  - 挂载 `MonitoringMiddleware`，统一采集请求指标与结构化日志
  - 使用 `Instrumentator` 自动暴露 `/metrics` 指标端点
  - 将 `RequestLimiter.limiter` 注册为 slowapi 的全局 limiter，并挂接异常处理
  - 调用 `create_routes(app, request_limiter)` 注册所有路由
- **lifespan 生命周期管理**
  - 在启动阶段：
    - 根据 `AppConfig.vllm` 创建 `VLLMManager`
    - 如果 `auto_start=True` 且 vLLM 未运行，则调用 `VLLMManager.start()` 启动 vLLM，并通过 HTTP 轮询 `wait_for_ready()` 等待就绪
    - 使用 `log_manager.clean_old_logs()` 清理历史日志，并对 `fastapi.log` / `vllm.log` 做一次大小检查和轮转
    - 启动后台协程任务 `setup_log_rotation()`，定期轮转与清理日志
  - 在关闭阶段：
    - 取消后台日志轮转任务
    - 如当前进程启动过 vLLM，则调用 `VLLMManager.stop()` 优雅停止

### 2. 配置与模型（`models.py` + `config_manager.py`）

- **配置模型（`models.py`）**
  - `APIKeyInfo`：描述单个 API Key（key/user/quota/enabled）
  - `RateLimitConfig`：QPS / 并发 / 每分钟 Token 限制
  - `LoRAPreloadModule` / `LoRARuntimeResolver` / `LoRASettings`：
    - 描述预加载 LoRA 模块、运行时 resolver、最大 LoRA 数量等
  - `PythonLauncherConfig` / `VLLMLaunchMode` / `VLLMConfig`：
    - 控制 vLLM 的启动模式（python_api / cli）、日志路径、PID 文件、conda 环境、额外环境变量等
  - `AppConfig`：
    - 顶层应用配置，包含 vLLM/FastAPI 端口、日志级别、API key 文件路径、`RateLimitConfig` 与 `VLLMConfig`
- **配置管理（`config_manager.py`）**
  - `load_config()`：
    - 从 `CONFIG_FILE` 或默认 `config/config.yaml` 读取 YAML
    - 如文件不存在，会根据 `AppConfig()` 默认值生成一个新的配置文件
  - `init_config()`：
    - 初始化全局 `app_config` 单例
  - `get_config()`：
    - 在任意模块中按需获取当前配置（懒加载）

### 3. 认证与权限（`auth.py`）

- `APIKeyAuth`：
  - 从 `AppConfig.api_keys_file`（默认 `config/api_keys.json`）中加载 key 列表
  - 如文件不存在，会自动创建一个包含 `sk-default-key-change-me` 的默认文件
  - `verify_key(api_key)`：校验 key 是否存在且 `enabled == True`，返回 `APIKeyInfo` 或 `None`
  - `reload_keys()`：支持运行时重新加载 key 文件
- FastAPI 依赖 `verify_api_key()`：
  - 从 `Authorization` 头中解析：
    - `Bearer sk-xxx`
    - 或直接 `sk-xxx`
  - 通过全局 `auth_manager` 校验 key
  - 当对应 `APIKeyInfo.user == "admin"` 时，认为是管理员，可访问 `/admin/*` 端点

### 4. 限流与并发控制（`limiter.py`）

- `RequestLimiter`：
  - 使用 slowapi 的 `Limiter` 实现 **按 IP QPS 限制**
    - 当 `RateLimitConfig.qps` 不为 `None` 时，在路由上通过装饰器套用 `limit("{qps}/second")`
  - 使用 `asyncio.Semaphore` 实现 **并发控制**
    - `global_semaphore`：全局并发上限（`concurrent is None` 时视为不限制）
    - `per_key_semaphores`：按 API key 维护的并发上限
    - `check_concurrent_limit(api_key)` / `release_concurrent_limit(api_key)`：
      - 在业务处理前后调用，立即尝试获取/释放 semaphore，并在超时（默认 1ms）时返回 429
  - 使用内存中的时间窗口队列实现 **每分钟 Token 限制**
    - `token_usage[api_key]`：记录最近 60 秒内各次请求使用的 token 数
    - `check_token_limit(api_key, tokens)`：超过 `tokens_per_minute` 时返回 429

### 5. 监控与日志（`monitoring.py` + `log_manager.py`）

- Prometheus 指标（`monitoring.py`）：
  - `http_requests_total(method, endpoint, status_code)`
  - `http_request_duration_seconds(method, endpoint)`
  - `http_active_requests`
  - `http_errors_total(method, endpoint, error_type)`
  - `token_usage_total(api_key, type=input|output)`
- 监控中间件 `MonitoringMiddleware`：
  - 包裹所有请求，记录：
    - 请求计数、时长、活跃请求数
    - 错误统计
    - 结构化 JSON 日志（包含 method/endpoint/status/duration/client_ip 等）
- Token 使用记录：
  - `record_token_usage(api_key, input_tokens, output_tokens)`：
    - 增加 Prometheus Counter
    - 打印一条结构化日志，便于审计与计费
- 日志文件管理（`log_manager.py`）：
  - `rotate_log_file(log_file, max_size_mb)`：单文件按大小轮转（重命名为 `.timestamp`）
  - `clean_old_logs(log_dir, days_to_keep)`：按最后修改时间删除旧的轮转日志
  - `get_log_stats(log_dir)`：统计日志总大小、数量、最早/最新文件
  - `setup_log_rotation(log_dir, max_size_mb, days_to_keep, check_interval_hours)`：
    - 返回一个协程函数，用于在后台周期性轮转和清理

### 6. vLLM 进程管理（`vllm_manager.py`）

- `VLLMManager(AppConfig)`：
  - 管理 vLLM OpenAI 兼容服务的整个生命周期
  - 按配置确保 PID 目录、日志目录存在
- 启动逻辑：
  - 根据 `VLLMConfig.launch_mode` 与 `python_launcher.enabled` 决定：
    - **python_api 模式（推荐）**：
      - 通过 multiprocessing 子进程调用 `vllm.entrypoints.openai.api_server.serve`
      - 使用 `_extract_vllm_args()` 从启动命令中抽取 vLLM 的实际参数
    - **cli 模式**：
      - 构造 shell 命令（可选通过 conda 环境执行）并 `subprocess.Popen`
  - 启动前注入：
    - 额外环境变量 `extra_env`
    - LoRA 相关环境变量（`VLLM_ALLOW_RUNTIME_LORA_UPDATING` / `VLLM_PLUGINS` / `VLLM_LORA_RESOLVER_CACHE_DIR`）
    - LoRA CLI 参数（`--enable-lora`、`--lora-modules` 等）
  - 将子进程 PID 写入 `VLLMConfig.pid_file`
- 运行状态与停止：
  - `is_running()`：优先检查内部子进程句柄，其次检查 PID 文件对应的进程是否仍在
  - `wait_for_ready(host, port, timeout)`：
    - 周期性访问 `http://{host}:{port}/health` 与 `/v1/models`
    - 首个 200 视为就绪
  - `stop(force=False)`：
    - 优先终止内部 multiprocessing/proc
    - 如必要，读取 PID 文件并向目标进程发送 SIGTERM / SIGKILL

### 7. 与 vLLM 的 HTTP 通信（`vllm_client.py`）

- 全局 `httpx.AsyncClient` 连接池：
  - `max_keepalive_connections=1024` / `max_connections=2048`
  - 适配高并发、长时间流式响应的场景
- 请求转发函数：
  - `forward_stream_request(vllm_url, body, api_key)`：
    - 使用 `client.stream("POST", ...)` 直接以字节流透传 SSE 数据
    - 对部分 chunk 进行采样写入日志，捕获 vLLM 返回错误
    - 在流结束后异步调用 `process_stream_monitoring()` 尝试从 SSE 中解析 usage 字段并记录 Token 使用
  - `forward_non_stream_request(vllm_url, body, api_key)`：
    - 普通 JSON POST 调用
    - 校验状态码并安全解析 JSON（失败时记录响应预览）
    - 若响应中包含 `usage` 字段，则调用 `record_token_usage()`
  - `forward_get_request(vllm_url)`：
    - 用于 `/v1/models` 等 GET 请求

### 8. 路由与业务入口（`routes.py`）

- 健康与指标：
  - `GET /health`：返回服务状态与 vLLM 目标地址
  - `GET /metrics`：由 Instrumentator 自动挂载（函数体留空）
- 推理相关端点（均依赖 `verify_api_key` 与可选 QPS 装饰器）：
  - `POST /v1/chat/completions` 与 `/chat/completions`
    - 在进入 vLLM 前：
      - 记录请求开始日志
      - 调用 `RequestLimiter.check_concurrent_limit(api_key)` 做并发控制
      - 粗略估算输入 Token 数并调用 `check_token_limit()`
    - 根据 `stream` 标志选择：
      - 调用 `forward_stream_request()` 处理流式 SSE
      - 或 `forward_non_stream_request()` 返回一次性 JSON
    - 在 finally 中始终调用 `release_concurrent_limit()`
  - `POST /v1/completions` 与 `/completions`
    - 基于 `prompt` 长度估算 Token 数
    - 调用 `forward_non_stream_request()` 转发
  - `GET /v1/models` 与 `/models`
    - 直接调用 `forward_get_request()` 获取 vLLM 模型列表
- 管理端点（仅 `user == "admin"` 的 Key 可访问）：
  - `POST /admin/reload-keys`：
    - 使用 `APIKeyAuth` 重新加载 `api_keys.json`
  - `POST /admin/clean-logs?days=7`：
    - 调用 `log_manager.clean_old_logs()` 并返回清理结果 + 最新统计
  - `GET /admin/log-stats`：
    - 直接返回 `get_log_stats()` 的结果
  - `POST /admin/load-lora-adapter` / `POST /admin/unload-lora-adapter`：
    - 将请求体转发到 vLLM 的 `/v1/load_lora_adapter` / `/v1/unload_lora_adapter`

## 代码依赖

主要三方依赖见根目录 `requirements.txt`：

- FastAPI / Uvicorn：Web 框架与 ASGI 服务
- slowapi：QPS 速率限制
- prometheus-fastapi-instrumentator / prometheus-client：指标采集与导出
- httpx：高并发 HTTP 客户端
- pydantic：配置与请求/响应模型
- structlog：结构化 JSON 日志
- PyYAML：配置文件解析

