# 快速开始指南

本文档介绍如何快速启动和使用 vLLM Proxy 服务。

## 环境要求

- Python 3.8+（建议 3.10+）
- Linux 环境（vLLM 仅支持 Linux，因此本项目仅在 Linux 上运行）
- Conda（可选）或 Python 虚拟环境
- vLLM 运行环境（GPU/驱动/依赖等由 vLLM 决定）
- Nginx（可选，通常用于生产反向代理）
- Prometheus 和 Grafana（可选，用于监控）

## 安装依赖

```bash
# 激活Conda环境（如果使用）
conda activate Jeff-py312

# 安装Python依赖
pip install -r requirements.txt
```

## 配置

### 主配置文件 (`config/config.yaml`)

```yaml
# vLLM服务配置
vllm_host: localhost
vllm_port: 8002

# sglang服务配置（可选）
sglang_host: localhost
sglang_port: 8003

# FastAPI服务配置
fastapi_host: 0.0.0.0
fastapi_port: 8001

# API Keys文件路径
api_keys_file: config/api_keys.json

# 请求限制配置
rate_limit:
  qps: null                  # 每秒请求数限制（null 表示不限制）
  concurrent: null           # 并发连接数限制（null 表示不限制）
  tokens_per_minute: null    # 每分钟 token 数限制（null 表示不限制）

# 日志级别
log_level: INFO

# 模型路由：模型名 -> 后端类型（vllm/sglang）
# 若为空，则 FastAPI 会尝试从已启动的后端自动发现模型（通过 /v1/models）。
model_backend_mapping: {}

# vLLM Python 启动与 LoRA 管理
vllm:
  auto_start: false                  # FastAPI 启动时是否自动启动 vLLM
  launch_mode: python_api            # python_api（推荐）| cli
  start_cmd_file: config/vllm_start_cmd.txt
  start_cmd: null                    # 可选：直接在此写入启动命令字符串（优先于 start_cmd_file）
  log_file: logs/vllm.log
  pid_file: .pids/vllm.pid
  extra_env: {}                      # 透传给 vLLM 进程的额外环境变量
  lora:
    enabled: true
    max_lora_rank: 64
    max_loras: 4
    max_cpu_loras: 2
    preload: []                      # 预加载 LoRA 模块列表（name/path/base_model_name）
    default_mm_loras: {}             # 多模态场景的默认 LoRA 映射
    limit_mm_per_prompt: {}          # 多模态 LoRA 的限流策略
    runtime_resolver:
      allow_runtime_updates: true    # 启用 /v1/load_lora_adapter 与 /v1/unload_lora_adapter
      plugins:
        - lora_filesystem_resolver
      cache_dir: ./lora_cache

# sglang 启动配置（可选）
sglang:
  auto_start: false
  launch_mode: python_api
  start_cmd_file: config/sglang_start_cmd.txt
  start_cmd: null
  log_file: logs/sglang.log
  pid_file: .pids/sglang.pid
  extra_env: {}
```

### API Keys配置 (`config/api_keys.json`)

```json
{
  "keys": [
    {
      "key": "sk-your-api-key-here",
      "user": "user1",
      "quota": 10000,
      "enabled": true
    }
  ]
}
```

**提示**：

- 如果 `config/api_keys.json` 不存在，服务启动时会自动创建一个默认 key：`sk-default-key-change-me`（请立即替换）。
- 管理端点（`/admin/*`）使用一个非常简单的规则：`api_keys.json` 里该 key 对应的 `user` 必须等于 `"admin"`。

### vLLM启动命令 (`config/vllm_start_cmd.txt`)

已预配置您的 vLLM 启动命令，可根据需要修改。

### LoRA配置与动态切换

- 在 `vllm.lora.preload` 中列出需要开机即加载的 LoRA：

  ```yaml
  vllm:
    lora:
      preload:
        - name: sql-lora
          path: /data/lora/sql
          base_model_name: meta-llama/Llama-2-7b-hf
  ```

- API 请求中的 `model` 字段可填写基座模型或任意已加载的 LoRA 名称，系统会自动路由至对应权重。
- 若 `runtime_resolver.allow_runtime_updates: true`，可通过 vLLM 原生端点动态加载/卸载 LoRA：

  ```bash
  # 加载 LoRA
  curl -X POST http://localhost:8002/v1/load_lora_adapter \
    -H "Content-Type: application/json" \
    -d '{"lora_name": "sql_adapter", "lora_path": "/data/lora/sql"}'

  # 卸载 LoRA
  curl -X POST http://localhost:8002/v1/unload_lora_adapter \
    -H "Content-Type: application/json" \
    -d '{"lora_name": "sql_adapter"}'
  ```

> 默认启用 `lora_filesystem_resolver` 插件，可将 LoRA 目录放入 `vllm.lora.runtime_resolver.cache_dir/{$lora_name}`，系统会在首次请求时自动加载。

### 管理端点（需 admin API Key）

- 重载 API Keys：`POST /admin/reload-keys`
- 清理日志：`POST /admin/clean-logs?days=7`
- 查看日志统计：`GET /admin/log-stats`
- 动态加载 LoRA：`POST /admin/load-lora-adapter`（请求体与 vLLM `/v1/load_lora_adapter` 一致）
- 动态卸载 LoRA：`POST /admin/unload-lora-adapter`

## 启动服务

### 方式1: 使用启动脚本（推荐）

```bash
# 启动所有服务
./scripts/start.sh

# 脚本会自动：
# 1. 通过 Python 启动器启动 vLLM（支持 LoRA 配置与动态接口）
# 2. 启动FastAPI代理服务
# 3. 可选启动Nginx
```

若只想单独运行 vLLM，可直接调用 Python 启动器：

```bash
python scripts/start_vllm.py --wait
```

### 方式2: 手动启动

```bash
# 1. 启动vLLM（在Conda环境中）
conda activate Jeff-py312
python -m vllm.entrypoints.openai.api_server \
  --tokenizer-mode auto \
  --model /root/sj-tmp/LLM/Qwen3-80B-A3B/ \
  --dtype bfloat16 \
  -tp 6 \
  --disable-log-requests \
  --port 8002 \
  --gpu 0.9 \
  --max-num-seqs 512 \
  --served-model-name Qwen3-80B-A3B \
  --enable-prefix-caching

# 2. 启动FastAPI（在另一个终端）
conda activate Jeff-py312
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 3. 启动Nginx（可选）
sudo systemctl start nginx
```

## 停止服务

```bash
./scripts/stop.sh
# 或仅停止 vLLM
python scripts/start_vllm.py --stop
```

## API 使用

### 认证

所有 API 请求需要在请求头中包含 API Key:

```bash
Authorization: Bearer sk-your-api-key-here
```

或者:

```bash
Authorization: sk-your-api-key-here
```

### 端点

**访问方式**:
- 直接访问FastAPI: `http://localhost:8001`
- 通过Nginx访问: `http://localhost:8000`（如果配置了Nginx）

**兼容路径**（等价于 `/v1/*`）：

- `POST /chat/completions`
- `POST /completions`
- `GET /models`

#### Chat Completions

```bash
# 直接访问FastAPI
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

```bash
# 或通过Nginx访问
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

#### Completions

```bash
curl -X POST http://localhost:8001/v1/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "prompt": "The capital of France is"
  }'
```

#### 列出模型

```bash
curl http://localhost:8001/v1/models \
  -H "Authorization: Bearer sk-your-api-key-here"
```

#### 健康检查

```bash
# 健康检查端点（无需认证）
curl http://localhost:8001/health
```

## 故障排查

### 服务无法启动

1. 检查端口占用:

```bash
lsof -i :8000  # Nginx端口
lsof -i :8001  # FastAPI端口
lsof -i :8002  # vLLM端口
```

2. 检查日志:

```bash
tail -f logs/fastapi.log
tail -f logs/vllm.log
```

3. 检查Conda环境:

```bash
conda activate Jeff-py312
python --version
```

### API Key 认证失败

1. 检查 API key 格式是否正确
2. 确认 API key 在 `config/api_keys.json` 中且 `enabled: true`
3. 检查请求头格式: `Authorization: Bearer sk-xxx`

### 请求被限流

所有限流都在 FastAPI 层实现，如果遇到 429 错误：

1. **QPS 超限**: 检查当前 QPS 是否超过 `config/config.yaml` 中设置的 `rate_limit.qps`
2. **并发超限**: 检查当前并发连接数是否超过 `rate_limit.concurrent`
3. **Token 超限**: 如果启用了 token 限制，检查是否超过 `rate_limit.tokens_per_minute`
4. **查看日志**: 检查 `logs/fastapi.log` 了解具体限制原因和详细信息

**提示**: 可以通过调整 `config/config.yaml` 中的限制参数来修改限制策略，无需重启 Nginx。

### Prometheus 无法抓取指标

1. 确认 FastAPI 服务运行在 8001 端口
2. 访问 http://localhost:8001/metrics 验证指标端点
3. 检查 Prometheus 配置中的 targets 设置

## 相关文档

- `docs/START_GUIDE.md`：启动脚本使用指南
- `docs/LOG_ROTATION.md`：日志轮转说明
- `docs/NGINX_SETUP.md`：Nginx 配置指南
- `docs/MONITORING_SETUP.md`：监控配置指南
- `docs/LORA_USAGE_EXAMPLES.md`：LoRA 使用示例

# 快速开始指南

本文档介绍如何快速启动和使用 vLLM Proxy 服务。

## 环境要求

- Python 3.8+（建议 3.10+）
- Linux 环境（vLLM 仅支持 Linux，因此本项目仅在 Linux 上运行）
- Conda（可选）或 Python 虚拟环境
- vLLM 运行环境（GPU/驱动/依赖等由 vLLM 决定）
- Nginx（可选，通常用于生产反向代理）
- Prometheus 和 Grafana（可选，用于监控）

## 安装依赖

```bash
# 激活Conda环境（如果使用）
conda activate Jeff-py312

# 安装Python依赖
pip install -r requirements.txt
```

## 配置

### 主配置文件 (`config/config.yaml`)

```yaml
# vLLM服务配置
vllm_host: localhost
vllm_port: 8002

# FastAPI服务配置
fastapi_host: 0.0.0.0
fastapi_port: 8001

# API Keys文件路径
api_keys_file: config/api_keys.json

# 请求限制配置
rate_limit:
  qps: null                  # 每秒请求数限制（null 表示不限制）
  concurrent: null           # 并发连接数限制（null 表示不限制）
  tokens_per_minute: null    # 每分钟 token 数限制（null 表示不限制）

# 日志级别
log_level: INFO

# vLLM Python 启动与 LoRA 管理
vllm:
  auto_start: false                  # FastAPI 启动时是否自动启动 vLLM
  launch_mode: python_api            # python_api（推荐）| cli
  start_cmd_file: config/vllm_start_cmd.txt
  start_cmd: null                    # 可选：直接在此写入启动命令字符串（优先于 start_cmd_file）
  log_file: logs/vllm.log
  pid_file: .pids/vllm.pid
  extra_env: {}                      # 透传给 vLLM 进程的额外环境变量
  lora:
    enabled: true
    max_lora_rank: 64
    max_loras: 4
    max_cpu_loras: 2
    preload: []                      # 预加载 LoRA 模块列表（name/path/base_model_name）
    default_mm_loras: {}             # 多模态场景的默认 LoRA 映射
    limit_mm_per_prompt: {}          # 多模态 LoRA 的限流策略
    runtime_resolver:
      allow_runtime_updates: true    # 启用 /v1/load_lora_adapter 与 /v1/unload_lora_adapter
      plugins:
        - lora_filesystem_resolver
      cache_dir: ./lora_cache
```

### API Keys配置 (`config/api_keys.json`)

```json
{
  "keys": [
    {
      "key": "sk-your-api-key-here",
      "user": "user1",
      "quota": 10000,
      "enabled": true
    }
  ]
}
```

**提示**：

- 如果 `config/api_keys.json` 不存在，服务启动时会自动创建一个默认 key：`sk-default-key-change-me`（请立即替换）。
- 管理端点（`/admin/*`）使用一个非常简单的规则：`api_keys.json` 里该 key 对应的 `user` 必须等于 `"admin"`。

### vLLM启动命令 (`config/vllm_start_cmd.txt`)

已预配置您的vLLM启动命令，可根据需要修改。

### LoRA配置与动态切换

- 在 `vllm.lora.preload` 中列出需要开机即加载的 LoRA：

  ```yaml
  vllm:
    lora:
      preload:
        - name: sql-lora
          path: /data/lora/sql
          base_model_name: meta-llama/Llama-2-7b-hf
  ```

- API 请求中的 `model` 字段可填写基座模型或任意已加载的 LoRA 名称，系统会自动路由至对应权重。
- 若 `runtime_resolver.allow_runtime_updates: true`，可通过 vLLM 原生端点动态加载/卸载 LoRA：

  ```bash
  # 加载 LoRA
  curl -X POST http://localhost:8002/v1/load_lora_adapter \
    -H "Content-Type: application/json" \
    -d '{"lora_name": "sql_adapter", "lora_path": "/data/lora/sql"}'

  # 卸载 LoRA
  curl -X POST http://localhost:8002/v1/unload_lora_adapter \
    -H "Content-Type: application/json" \
    -d '{"lora_name": "sql_adapter"}'
  ```

> 默认启用 `lora_filesystem_resolver` 插件，可将 LoRA 目录放入 `vllm.lora.runtime_resolver.cache_dir/{$lora_name}`，系统会在首次请求时自动加载。

### 管理端点（需 admin API Key）

- 重载 API Keys：`POST /admin/reload-keys`
- 清理日志：`POST /admin/clean-logs?days=7`
- 查看日志统计：`GET /admin/log-stats`
- 动态加载 LoRA：`POST /admin/load-lora-adapter`（请求体与 vLLM `/v1/load_lora_adapter` 一致）
- 动态卸载 LoRA：`POST /admin/unload-lora-adapter`
- 需启用 `vllm.lora.runtime_resolver.allow_runtime_updates: true`，系统会自动注入 `VLLM_ALLOW_RUNTIME_LORA_UPDATING`、`VLLM_PLUGINS`、`VLLM_LORA_RESOLVER_CACHE_DIR`。

## 启动服务

### 方式1: 使用启动脚本（推荐）

```bash
# 启动所有服务
./scripts/start.sh

# 脚本会自动：
# 1. 通过 Python 启动器启动 vLLM（支持 LoRA 配置与动态接口）
# 2. 启动FastAPI代理服务
# 3. 可选启动Nginx
```

若只想单独运行 vLLM，可直接调用 Python 启动器：

```bash
python scripts/start_vllm.py --wait
```

### 方式2: 手动启动

```bash
# 1. 启动vLLM（在Conda环境中）
conda activate Jeff-py312
python -m vllm.entrypoints.openai.api_server \
  --tokenizer-mode auto \
  --model /root/sj-tmp/LLM/Qwen3-80B-A3B/ \
  --dtype bfloat16 \
  -tp 6 \
  --disable-log-requests \
  --port 8002 \
  --gpu 0.9 \
  --max-num-seqs 512 \
  --served-model-name Qwen3-80B-A3B \
  --enable-prefix-caching

# 2. 启动FastAPI（在另一个终端）
conda activate Jeff-py312
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 3. 启动Nginx（可选）
sudo systemctl start nginx
```

## 停止服务

```bash
./scripts/stop.sh
# 或仅停止 vLLM
python scripts/start_vllm.py --stop
```

## API使用

### 认证

所有API请求需要在请求头中包含API Key:

```bash
Authorization: Bearer sk-your-api-key-here
```

或者:

```bash
Authorization: sk-your-api-key-here
```

### 端点

**访问方式**:
- 直接访问FastAPI: `http://localhost:8001`
- 通过Nginx访问: `http://localhost:8000`（如果配置了Nginx）

**兼容路径**（等价于 `/v1/*`）：

- `POST /chat/completions`
- `POST /completions`
- `GET /models`

#### Chat Completions

```bash
# 直接访问FastAPI
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

```bash
# 或通过Nginx访问
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

#### Completions

```bash
curl -X POST http://localhost:8001/v1/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "prompt": "The capital of France is"
  }'
```

#### 列出模型

```bash
curl http://localhost:8001/v1/models \
  -H "Authorization: Bearer sk-your-api-key-here"
```

#### 健康检查

```bash
# 健康检查端点（无需认证）
curl http://localhost:8001/health
```

## 故障排查

### 服务无法启动

1. 检查端口占用:

```bash
lsof -i :8000  # Nginx端口
lsof -i :8001  # FastAPI端口
lsof -i :8002  # vLLM端口
```

2. 检查日志:
```bash
tail -f logs/fastapi.log
tail -f logs/vllm.log
```

3. 检查Conda环境:
```bash
conda activate Jeff-py312
python --version
```

### API Key认证失败

1. 检查API key格式是否正确
2. 确认API key在 `config/api_keys.json` 中且 `enabled: true`
3. 检查请求头格式: `Authorization: Bearer sk-xxx`

### 请求被限流

所有限流都在FastAPI层实现，如果遇到429错误：

1. **QPS超限**: 检查当前QPS是否超过 `config/config.yaml` 中设置的 `rate_limit.qps`
2. **并发超限**: 检查当前并发连接数是否超过 `rate_limit.concurrent`
3. **Token超限**: 如果启用了token限制，检查是否超过 `rate_limit.tokens_per_minute`
4. **查看日志**: 检查 `logs/fastapi.log` 了解具体限制原因和详细信息

**提示**: 可以通过调整 `config/config.yaml` 中的限制参数来修改限制策略，无需重启Nginx。

### Prometheus无法抓取指标

1. 确认FastAPI服务运行在8001端口
2. 访问 http://localhost:8001/metrics 验证指标端点
3. 检查Prometheus配置中的targets设置

## 相关文档

- [启动脚本使用指南](./START_GUIDE.md)
- [日志轮转说明](./LOG_ROTATION.md)
- [Nginx配置指南](./NGINX_SETUP.md)
- [监控配置指南](./MONITORING_SETUP.md)
- [LoRA使用示例](./LORA_USAGE_EXAMPLES.md)
