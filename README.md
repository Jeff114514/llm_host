# vLLM Proxy - FastAPI + vLLM + Nginx 全栈部署方案

完整的本地LLM部署方案，包含API key认证、请求限制和监控功能。

## 架构概览

```
Client → Nginx (反向代理) → FastAPI (认证/限流/监控) → vLLM (OpenAI API)
```

**注意**: 所有控制逻辑（认证、限流、监控）都在FastAPI中处理，Nginx仅作为反向代理。

## 功能特性

- ✅ **API Key认证**: 基于文件的API key管理，支持多key和配额管理
- ✅ **请求限制**: QPS限制、并发连接数限制、Token使用量限制（全部在FastAPI中实现）
- ✅ **监控指标**: Prometheus指标收集，Grafana可视化仪表板
- ✅ **日志记录**: 结构化日志，请求/响应追踪
- ✅ **Nginx集成**: 纯反向代理，所有控制逻辑集中在FastAPI
- ✅ **Conda支持**: 支持Conda环境管理

## 快速开始

### 1. 环境要求

- Python 3.8+
- Conda（推荐）或Python虚拟环境
- Nginx（可选）
- Prometheus和Grafana（可选，用于监控）

### 2. 安装依赖

```bash
# 激活Conda环境（如果使用）
conda activate Jeff-py312

# 安装Python依赖
pip install -r requirements.txt
```

### 3. 配置

#### 3.1 主配置文件 (`config/config.yaml`)

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
  qps: 10                    # 每秒请求数限制
  concurrent: 5             # 并发连接数限制
  tokens_per_minute: null    # 每分钟token数限制（null表示不限制）

# 日志级别
log_level: INFO
```

#### 3.2 API Keys配置 (`config/api_keys.json`)

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

#### 3.3 vLLM启动命令 (`config/vllm_start_cmd.txt`)

已预配置您的vLLM启动命令，可根据需要修改。

### 4. 启动服务

#### 方式1: 使用启动脚本（推荐）

```bash
# 启动所有服务
./scripts/start.sh

# 脚本会自动：
# 1. 启动vLLM服务（使用Conda环境）
# 2. 启动FastAPI代理服务
# 3. 可选启动Nginx
```

#### 方式2: 手动启动

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

### 5. 停止服务

```bash
./scripts/stop.sh
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

## Nginx配置

### 安装Nginx

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install nginx

# CentOS/RHEL
sudo yum install nginx
```

### 配置Nginx

1. 复制配置文件:
```bash
sudo cp nginx/nginx.conf /etc/nginx/sites-available/vllm_proxy
sudo ln -s /etc/nginx/sites-available/vllm_proxy /etc/nginx/sites-enabled/
```

2. 测试配置:
```bash
sudo nginx -t
```

3. 重启Nginx:
```bash
sudo systemctl restart nginx
```

### Nginx配置说明

Nginx仅作为反向代理，**不进行任何限流或认证**。所有控制逻辑都在FastAPI中处理。

**配置特点**:
- **反向代理**: 监听8000端口，所有请求转发到FastAPI（端口8001）
- **超时设置**: 300秒（适合LLM长文本生成）
- **流式支持**: 关闭缓冲以支持流式响应
- **WebSocket支持**: 支持WebSocket连接

**优势**:
- 统一管理：所有控制逻辑集中在FastAPI
- 更灵活：可以动态调整限制策略
- 更精确：可以基于API key、用户等维度进行限制

详细配置说明请参考 `nginx/README.md`

## 监控配置

### Prometheus

1. 下载并安装Prometheus: https://prometheus.io/download/

2. 复制配置文件:
```bash
cp monitoring/prometheus.yml /path/to/prometheus/prometheus.yml
```

3. 启动Prometheus:
```bash
./prometheus --config.file=prometheus.yml
```

4. 访问: http://localhost:9090

### Grafana

1. 安装Grafana:
```bash
# Ubuntu/Debian
sudo apt-get install grafana
sudo systemctl start grafana-server

# CentOS/RHEL
sudo yum install grafana
sudo systemctl start grafana-server
```

2. 访问: http://localhost:3000（默认用户名/密码: admin/admin）

3. 配置数据源:
   - 进入 Configuration > Data Sources
   - 添加Prometheus数据源: http://localhost:9090

4. 导入仪表板:
   - 进入 Dashboards > Import
   - 上传 `monitoring/grafana/dashboard.json`

详细监控配置说明请参考 `monitoring/README.md`

## 监控指标

### HTTP指标

- `http_requests_total`: 总请求数（按方法、端点、状态码）
- `http_request_duration_seconds`: 请求持续时间（直方图）
- `http_active_requests`: 活跃请求数（仪表）
- `http_errors_total`: 错误总数（按错误类型）

### Token指标

- `token_usage_total`: Token使用总量（按API key和类型）

### 访问指标

- Prometheus: http://localhost:9090/metrics
- FastAPI指标: http://localhost:8001/metrics

## 请求限制

所有请求限制都在FastAPI中实现，包括：

### QPS限制

每秒请求数限制，默认10请求/秒。可在 `config/config.yaml` 中配置。

**特点**:
- 基于时间窗口的速率限制
- 使用slowapi库实现
- 可针对不同端点设置不同限制

### 并发限制

全局和每个API key的并发连接数限制，默认5个并发连接。

**特点**:
- 全局并发限制：保护系统资源
- 每key并发限制：防止单个用户占用过多资源
- 使用asyncio.Semaphore实现

### Token限制

每分钟Token使用量限制（可选），可在配置文件中启用。

**特点**:
- 基于时间窗口（1分钟）
- 分别统计输入和输出token
- 可在配置文件中设置 `tokens_per_minute`

**注意**: Nginx不进行任何限流，所有限制都在FastAPI层实现，便于统一管理和动态调整。

## API Key管理

### 添加API Key

编辑 `config/api_keys.json`:

```json
{
  "keys": [
    {
      "key": "sk-new-key",
      "user": "user2",
      "quota": 5000,
      "enabled": true
    }
  ]
}
```

### 重新加载API Keys

无需重启服务，调用管理端点:

```bash
curl -X POST http://localhost:8001/admin/reload-keys \
  -H "Authorization: Bearer sk-your-api-key-here"
```

## 日志

### 日志位置

- FastAPI日志: `logs/fastapi.log`
- vLLM日志: `logs/vllm.log`
- Nginx访问日志: `/var/log/nginx/vllm_proxy_access.log`
- Nginx错误日志: `/var/log/nginx/vllm_proxy_error.log`

### 日志格式

FastAPI使用结构化日志（JSON格式），包含：
- 请求方法、端点、状态码
- 响应时间
- 客户端IP
- Token使用量

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

## 目录结构

```
LLMHOST/
├── app/                      # FastAPI应用
│   ├── __init__.py
│   ├── main.py              # 主应用
│   ├── auth.py              # API key认证
│   ├── limiter.py           # 请求限制
│   ├── monitoring.py        # 监控指标
│   └── models.py            # 数据模型
├── config/                   # 配置文件
│   ├── config.yaml          # 主配置
│   ├── api_keys.json        # API keys
│   └── vllm_start_cmd.txt   # vLLM启动命令
├── nginx/                    # Nginx配置
│   ├── nginx.conf           # Nginx配置
│   └── README.md            # Nginx说明
├── monitoring/               # 监控配置
│   ├── prometheus.yml       # Prometheus配置
│   ├── grafana/
│   │   └── dashboard.json   # Grafana仪表板
│   └── README.md            # 监控说明
├── scripts/                  # 脚本
│   ├── start.sh             # 启动脚本
│   └── stop.sh              # 停止脚本
├── logs/                     # 日志目录（自动创建）
├── .pids/                    # PID文件目录（自动创建）
├── requirements.txt          # Python依赖
└── README.md                # 本文档
```

## 技术栈

- **FastAPI**: Web框架，处理所有业务逻辑（认证、限流、监控）
- **vLLM**: LLM推理服务（OpenAI API兼容）
- **Nginx**: 纯反向代理，不进行限流或认证
- **Prometheus**: 指标收集
- **Grafana**: 监控可视化
- **slowapi**: 速率限制库
- **prometheus-fastapi-instrumentator**: Prometheus集成
- **structlog**: 结构化日志


