# FastAPI应用模块

FastAPI应用模块包含所有业务逻辑：API路由、认证、限流和监控。

## 模块结构

```
app/
├── __init__.py          # 模块初始化
├── main.py              # FastAPI主应用，路由定义
├── auth.py              # API Key认证模块
├── limiter.py           # 请求限制模块
├── monitoring.py        # 监控和指标收集
└── models.py            # 数据模型定义
```

## 模块说明

### main.py

FastAPI主应用，包含：

- **应用初始化**: 加载配置、初始化认证和限流模块
- **路由定义**: 
  - `/v1/chat/completions` - Chat Completions端点
  - `/v1/completions` - Completions端点
  - `/v1/models` - 列出可用模型
  - `/health` - 健康检查端点
  - `/metrics` - Prometheus指标端点
  - `/admin/reload-keys` - 重新加载API keys
- **中间件**: CORS、监控中间件
- **vLLM代理**: 转发请求到vLLM服务

**关键功能**:
- 请求转发到vLLM
- Token使用量记录
- 错误处理和日志记录

### auth.py

API Key认证模块，提供：

- **APIKeyAuth类**: API key管理器
  - `load_api_keys()`: 从文件加载API keys
  - `verify_key()`: 验证API key
  - `reload_keys()`: 重新加载keys（支持热重载）
- **verify_api_key()**: FastAPI依赖函数，用于路由认证

**支持的格式**:
- `Authorization: Bearer sk-xxx`
- `Authorization: sk-xxx`

**API Key文件格式** (`config/api_keys.json`):
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

### limiter.py

请求限制模块，实现三种限制：

1. **QPS限制**: 使用slowapi库实现每秒请求数限制
2. **并发限制**: 使用asyncio.Semaphore实现并发连接数限制
   - 全局并发限制
   - 每个API key的并发限制
3. **Token限制**: 基于时间窗口的Token使用量限制（每分钟）

**RequestLimiter类**:
- `check_concurrent_limit()`: 检查并发限制
- `release_concurrent_limit()`: 释放并发限制
- `check_token_limit()`: 检查Token限制

### monitoring.py

监控和指标收集模块：

- **Prometheus指标**:
  - `http_requests_total`: HTTP请求总数
  - `http_request_duration_seconds`: 请求持续时间
  - `http_active_requests`: 活跃请求数
  - `http_errors_total`: 错误总数
  - `token_usage_total`: Token使用量
- **MonitoringMiddleware**: 请求/响应监控中间件
- **结构化日志**: 使用structlog记录请求日志
- **record_token_usage()**: 记录Token使用量

### models.py

数据模型定义，使用Pydantic：

- **APIKeyInfo**: API Key信息模型
- **RateLimitConfig**: 速率限制配置模型
- **AppConfig**: 应用配置模型

## 配置

应用配置通过 `config/config.yaml` 加载，支持环境变量：

```bash
export CONFIG_FILE=/path/to/config.yaml
```

## 启动

### 开发模式

```bash
cd /root/sj-tmp/Jeff/LLMHOST
conda activate Jeff-py312
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### 生产模式

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4
```

或使用启动脚本：

```bash
./scripts/start.sh
```

## API端点

### 健康检查

```bash
curl http://localhost:8001/health
```

### Chat Completions

```bash
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 列出模型

```bash
curl http://localhost:8001/v1/models \
  -H "Authorization: Bearer sk-your-key"
```

### Prometheus指标

```bash
curl http://localhost:8001/metrics
```

## 扩展开发

### 添加新端点

在 `main.py` 中添加路由：

```python
@app.post("/v1/your-endpoint")
async def your_endpoint(
    request: Request,
    api_key_info: APIKeyInfo = Depends(verify_api_key)
):
    # 实现逻辑
    pass
```

### 添加新的限制类型

在 `limiter.py` 的 `RequestLimiter` 类中添加新方法：

```python
async def check_your_limit(self, api_key: str):
    # 实现限制逻辑
    pass
```

### 添加新的监控指标

在 `monitoring.py` 中定义新的Prometheus指标：

```python
your_metric = Counter(
    'your_metric_total',
    'Your metric description'
)
```

## 故障排查

### 认证失败

1. 检查API key格式是否正确
2. 确认key在 `config/api_keys.json` 中且 `enabled: true`
3. 查看日志: `logs/fastapi.log`

### 限流问题

1. 检查 `config/config.yaml` 中的限制配置
2. 查看日志了解具体限制原因
3. 调整限制参数

### 连接vLLM失败

1. 确认vLLM服务运行在配置的端口（默认8002）
2. 检查网络连接
3. 查看vLLM日志: `logs/vllm.log`

## 依赖

主要依赖见 `requirements.txt`:
- fastapi
- uvicorn
- slowapi
- prometheus-fastapi-instrumentator
- httpx
- pydantic
- structlog

