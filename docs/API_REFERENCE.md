 # 对外接口文档
 
 本文档描述 vLLM Proxy 对外暴露的 HTTP 接口、鉴权方式与典型请求示例。默认访问地址为 `http://{fastapi_host}:{fastapi_port}`（默认 `0.0.0.0:8001`），如经 Nginx 代理则通常是 `http://{nginx_host}:{nginx_port}`（示例 `localhost:8000`）。
 
 ## 鉴权
 
 - 所有业务接口（除 `/health`、`/metrics`）均需在请求头携带 API Key  
   - `Authorization: Bearer sk-xxx` 或 `Authorization: sk-xxx`
 - 管理接口（`/admin/*`）需使用 `config/api_keys.json` 中 `user == "admin"` 的 key。
 
 ## 公共约定
 
 - Content-Type：`application/json`
 - 兼容 OpenAI API 规范（请求/响应字段与官方保持一致）
 - 路径兼容性：`/v1/*` 与不带 `/v1` 前缀的等价路径均可用（如 `/v1/chat/completions` == `/chat/completions`）。
 
 ## 接口列表
 
 ### 健康与监控
 
- `GET /health`（无需鉴权）：检查所有后端服务的健康状态并返回整体状态
  
  **状态枚举值：**
  - `"healthy"`: 所有后端服务都正常
  - `"degraded"`: 部分后端服务不可用（至少有一个正常）
  - `"unhealthy"`: 所有后端服务都不可用或没有注册的后端
  
  **响应示例（所有后端正常）：**
  ```json
  {
    "status": "healthy",
    "service": "vLLM Proxy",
    "backends": {
      "http://localhost:8002": {
        "backend": "vllm",
        "base_url": "http://localhost:8002",
        "status": "healthy",
        "response": {...}
      },
      "http://localhost:8003": {
        "backend": "sglang",
        "base_url": "http://localhost:8003",
        "status": "healthy",
        "response": {...}
      }
    },
    "summary": {
      "total": 2,
      "healthy": 2,
      "unhealthy": 0
    },
    "vllm_url": "http://localhost:8002",
    "sglang_url": "http://localhost:8003"
  }
  ```
  
  **响应示例（部分后端不可用）：**
  ```json
  {
    "status": "degraded",
    "service": "vLLM Proxy",
    "backends": {
      "http://localhost:8002": {
        "backend": "vllm",
        "base_url": "http://localhost:8002",
        "status": "healthy",
        "response": {...}
      },
      "http://localhost:8003": {
        "backend": "sglang",
        "base_url": "http://localhost:8003",
        "status": "unhealthy",
        "error": "连接失败"
      }
    },
    "summary": {
      "total": 2,
      "healthy": 1,
      "unhealthy": 1
    },
    "vllm_url": "http://localhost:8002"
  }
  ```
  
  **说明：**
  - 接口会实际检查所有已注册的后端实例的 `/health` 端点
  - 每个后端有 5 秒的超时时间
  - 如果后端不可用，会在 `backends` 中记录错误信息
 - `GET /metrics`（无需鉴权）：Prometheus 指标出口，由 `prometheus-fastapi-instrumentator` 提供。
 
 ### 模型与推理
 
 - `POST /v1/chat/completions`  
   - 说明：OpenAI Chat Completions 兼容接口，按 `model` 字段自动路由至 vLLM 或 sglang。支持流式 `stream:true`。  
   - 关键字段：`model`（必填）、`messages`（必填）、`stream`（可选，默认 `false`）、其余参数与 OpenAI 一致（如 `temperature`、`top_p`、`max_tokens` 等）。  
   - 限制：按配置启用的 QPS / 并发 / 每分钟 Token 限流（见 `config/config.yaml`）。  
   - 非流式示例：
     ```bash
     curl -X POST http://localhost:8001/v1/chat/completions \
       -H "Authorization: Bearer sk-your-api-key" \
       -H "Content-Type: application/json" \
       -d '{
         "model": "Qwen3-80B-A3B",
         "messages": [{"role": "user", "content": "Hello!"}],
         "max_tokens": 200,
         "temperature": 0.7
       }'
     ```
   - 流式示例：
     ```bash
     curl -N -X POST http://localhost:8001/v1/chat/completions \
       -H "Authorization: Bearer sk-your-api-key" \
       -H "Content-Type: application/json" \
       -d '{
         "model": "Qwen3-80B-A3B",
         "messages": [{"role": "user", "content": "Hello!"}],
         "stream": true
       }'
     ```
 
 - `POST /v1/completions`  
   - 说明：OpenAI Completions 兼容接口，按 `model` 自动路由。  
   - 关键字段：`model`（必填）、`prompt`（必填）、可选 `max_tokens`、`temperature`、`top_p`、`stream` 等。  
   - 示例：
     ```bash
     curl -X POST http://localhost:8001/v1/completions \
       -H "Authorization: Bearer sk-your-api-key" \
       -H "Content-Type: application/json" \
       -d '{
         "model": "Qwen3-80B-A3B",
         "prompt": "The capital of France is",
         "max_tokens": 64
       }'
     ```
 
- `GET /v1/models`  
  - 说明：返回当前可用模型列表（OpenAI 兼容格式），聚合 vLLM 与 sglang。  
   - 示例：
     ```bash
     curl http://localhost:8001/v1/models \
       -H "Authorization: Bearer sk-your-api-key"
     ```
 
### 管理接口（需 admin Key）

- `POST /admin/reload-keys`：重新加载 `config/api_keys.json`。无请求体。  
- `POST /admin/clean-logs?days=7`：清理日志目录中早于 `days` 天的轮转文件，返回删除数量与释放空间统计。  
- `GET /admin/log-stats`：查看当前日志目录体积、数量、时间范围等统计信息。  
- `POST /admin/register-backend`：动态注册后端实例（支持多个 vLLM/sglang 实例）。请求体示例：
  ```json
  {
    "backend": "vllm",
    "base_url": "http://localhost:8004"
  }
  ```
- `POST /admin/unregister-backend`：注销后端实例。请求体示例：
  ```json
  {
    "base_url": "http://localhost:8004"
  }
  ```
- `GET /admin/list-backends`：列出所有已注册的后端实例。  
- `POST /admin/refresh-models`：从所有后端重新发现模型，更新路由映射。  
- `POST /admin/start-vllm` / `POST /admin/stop-vllm`：动态启动 / 停止默认 vLLM 实例（通过管理器启动），并自动刷新模型列表。  
- `POST /admin/start-sglang` / `POST /admin/stop-sglang`：动态启动 / 停止默认 sglang 实例（通过管理器启动），并自动刷新模型列表。  
- `GET /admin/backend-status`：查看默认后端运行状态、所有已注册的后端实例及当前可用模型。  
- `POST /admin/load-lora-adapter`：将请求体透传给 vLLM `/v1/load_lora_adapter`，用于动态加载 LoRA。请求体示例：
  ```json
  {
    "lora_name": "sql_adapter",
    "lora_path": "/data/lora/sql",
    "base_url": "http://localhost:8004"
  }
  ```
  其中 `base_url` 可选，不指定则使用默认 vLLM 实例。  
- `POST /admin/unload-lora-adapter`：透传到 vLLM `/v1/unload_lora_adapter`，用于卸载 LoRA。请求体示例：
  ```json
  {
    "lora_name": "sql_adapter",
    "base_url": "http://localhost:8004"
  }
  ```
  其中 `base_url` 可选，不指定则使用默认 vLLM 实例。
 
 ### 错误码与返回
 
 - `400`：请求体缺失关键字段（如 `model`）。  
 - `401`：API Key 缺失、无效，或非 admin 调用管理端点。  
 - `404`：`model` 未找到或未映射到后端。  
 - `429`：触发 QPS / 并发 / 每分钟 Token 限制。  
 - `500`：内部错误或后端异常。  
 
 ### 速率与并发控制
 
 - QPS：若 `rate_limit.qps` 配置非空，路由会自动套用限速器。  
 - 并发：全局与按 Key 的并发信号量，超限立即返回 429。  
 - Token/分钟：按 Key 维护 60 秒滑窗 Token 用量，超限返回 429。  
 
 ### 监控与日志
 
 - 监控：`/metrics` 暴露请求计数、时长、活跃请求、错误统计、Token 使用等指标。  
 - 日志：结构化 JSON 记录请求、限流、错误与 Token 计费信息；日志轮转与清理策略见 `docs/LOG_ROTATION.md`。  
 
 ## 接入检查清单
 
 - 已在请求头设置正确的 `Authorization`。  
 - `model` 名称存在且已映射到后端（如启用自动发现，可先调 `/v1/models`）。  
 - 根据业务需要选择 `stream` 模式，并处理 SSE 数据。  
 - 如遇 429，检查 `config/config.yaml` 的限流配置或降低并发。  
 - 管理操作需使用 admin Key，并确保 vLLM 已开启对应 LoRA 动态接口权限。  
