# 配置模块

配置模块包含所有配置文件，用于管理应用的各种设置。

## 文件结构

```
config/
├── config.yaml          # 主配置文件
├── api_keys.json        # API keys存储
└── vllm_start_cmd.txt   # vLLM启动命令
```

## 配置文件说明

### config.yaml

主配置文件，包含所有应用设置：

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

**配置项说明**:

- `vllm_host`: vLLM服务主机地址
- `vllm_port`: vLLM服务端口（默认8002）
- `fastapi_host`: FastAPI监听地址（0.0.0.0表示所有接口）
- `fastapi_port`: FastAPI监听端口（默认8001）
- `api_keys_file`: API keys文件路径（相对于项目根目录）
- `rate_limit.qps`: 每秒请求数限制
- `rate_limit.concurrent`: 并发连接数限制
- `rate_limit.tokens_per_minute`: 每分钟Token限制（null表示不限制）
- `log_level`: 日志级别（DEBUG, INFO, WARNING, ERROR）

**环境变量覆盖**:

可以通过环境变量指定配置文件路径：

```bash
export CONFIG_FILE=/path/to/custom/config.yaml
```

### api_keys.json

API keys存储文件，JSON格式：

```json
{
  "keys": [
    {
      "key": "sk-your-api-key-here",
      "user": "user1",
      "quota": 10000,
      "enabled": true
    },
    {
      "key": "sk-another-key",
      "user": "user2",
      "quota": 5000,
      "enabled": true
    }
  ]
}
```

**字段说明**:

- `key`: API key字符串（必需）
- `user`: 用户标识（可选，用于日志和监控）
- `quota`: 配额限制（可选，当前未使用，预留扩展）
- `enabled`: 是否启用（true/false）

**安全建议**:

1. 使用强随机字符串作为API key
2. 定期轮换API keys
3. 不要将包含真实keys的文件提交到版本控制
4. 使用文件权限限制访问：`chmod 600 config/api_keys.json`

**热重载**:

修改API keys后，无需重启服务，调用管理端点重新加载：

```bash
curl -X POST http://localhost:8001/admin/reload-keys \
  -H "Authorization: Bearer sk-your-key"
```

### vllm_start_cmd.txt

vLLM启动命令文件，包含完整的vLLM启动命令：

```
python -m vllm.entrypoints.openai.api_server --tokenizer-mode auto --model /root/sj-tmp/LLM/Qwen3-80B-A3B/ --dtype bfloat16 -tp 6 --disable-log-requests --port 8002 --gpu 0.9 --max-num-seqs 512 --served-model-name Qwen3-80B-A3B --enable-prefix-caching
```

**使用方式**:

启动脚本会自动读取此文件作为vLLM启动命令。如果需要修改：

1. 直接编辑此文件
2. 或通过环境变量覆盖：`export VLLM_START_CMD='your command'`
3. 或通过启动脚本参数传递：`./scripts/start.sh 'your command'`

**注意事项**:

- 确保端口与 `config.yaml` 中的 `vllm_port` 一致
- 确保模型路径正确
- 根据GPU配置调整 `--gpu` 和 `-tp` 参数

## 配置管理

### 创建自定义配置

1. 复制默认配置：
```bash
cp config/config.yaml config/config.custom.yaml
```

2. 修改配置项

3. 使用环境变量指定：
```bash
export CONFIG_FILE=config/config.custom.yaml
./scripts/start.sh
```

### 验证配置

应用启动时会自动验证配置，如果配置错误会显示错误信息。

### 配置优先级

1. 环境变量 `CONFIG_FILE`
2. 默认路径 `config/config.yaml`
3. 如果文件不存在，会创建默认配置

## 最佳实践

1. **生产环境配置**:
   - 使用强API keys
   - 设置合理的限制参数
   - 启用日志记录
   - 定期备份配置文件

2. **开发环境配置**:
   - 可以使用较宽松的限制
   - 启用DEBUG日志级别
   - 使用测试API keys

3. **安全配置**:
   - 限制配置文件访问权限
   - 不要将敏感信息提交到版本控制
   - 使用环境变量管理敏感配置

4. **性能调优**:
   - 根据实际负载调整QPS和并发限制
   - 监控Token使用量，合理设置限制
   - 根据服务器资源调整FastAPI workers数量

## 故障排查

### 配置加载失败

1. 检查配置文件路径是否正确
2. 检查YAML语法是否正确
3. 查看应用启动日志

### API key不生效

1. 检查 `api_keys.json` 格式是否正确
2. 确认key的 `enabled` 字段为 `true`
3. 调用 `/admin/reload-keys` 重新加载

### 端口冲突

1. 检查端口是否被占用：`lsof -i :8001`
2. 修改 `config.yaml` 中的端口配置
3. 确保vLLM端口与启动命令中的端口一致

