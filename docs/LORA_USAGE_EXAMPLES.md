# LoRA 和基准模型使用示例

本文档详细说明如何在不同请求中使用不同的 LoRA 适配器和基准模型。

## 核心概念

- **基准模型（Base Model）**：通过 `model` 参数指定，是基础的大语言模型
- **LoRA 适配器**：通过 vLLM 支持的 LoRA 参数指定，是对基准模型的微调适配器
- **每个请求独立**：每个请求都可以指定不同的模型和 LoRA 组合

## 重要说明（请先读）

1. **本项目的 FastAPI Proxy 不会改写你的请求体**：它仅做认证/限流/监控，然后把 JSON 原样转发给 vLLM 的 OpenAI 兼容端点。
2. **LoRA 的“请求参数字段名”取决于你正在使用的 vLLM 版本/启动参数**：不同版本可能支持不同字段（例如某些环境里会用 `lora_adapter_ids`，也可能只支持通过“模型名/已加载 LoRA 名称”进行路由）。
3. **最稳妥的动态加载/卸载方式**：
   - 直接调用 vLLM 原生端点：`POST /v1/load_lora_adapter`、`POST /v1/unload_lora_adapter`
   - 或调用本项目的管理端点（会转发到 vLLM，且需要 admin key）：`POST /admin/load-lora-adapter`、`POST /admin/unload-lora-adapter`

## 使用场景示例

### 场景1: 同一基准模型 + 不同 LoRA

适用于：使用同一个大模型，但需要针对不同任务使用不同的微调适配器。

```bash
# 请求1: SQL 生成任务
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "查询所有用户信息"}
    ],
    "lora_adapter_ids": ["sql_adapter"]
  }'

# 请求2: 代码生成任务（同一基准模型，不同LoRA）
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "写一个Python函数计算斐波那契数列"}
    ],
    "lora_adapter_ids": ["code_adapter"]
  }'

# 请求3: 翻译任务（同一基准模型，不同LoRA）
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "将以下文本翻译成英文：你好世界"}
    ],
    "adapter_id": "translation_adapter"
  }'
```

> 提醒：上面示例里的 `lora_adapter_ids` / `adapter_id` 仅作为“可能的请求字段”演示。请以你的 vLLM 版本文档/实际行为为准。

### 场景2: 不同基准模型 + 相同 LoRA

适用于：需要在不同规模或版本的模型上使用相同的微调适配器。

```bash
# 请求1: 使用大模型 + SQL LoRA
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "生成SQL查询"}
    ],
    "lora_adapter_ids": ["sql_adapter"]
  }'

# 请求2: 使用较小的模型 + 相同的 SQL LoRA
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen2.5-72B",
    "messages": [
      {"role": "user", "content": "生成SQL查询"}
    ],
    "lora_adapter_ids": ["sql_adapter"]
  }'
```

### 场景3: 不同基准模型 + 不同 LoRA

适用于：完全不同的任务组合，需要不同的模型和适配器。

```bash
# 请求1: 大模型 + SQL LoRA（复杂SQL任务）
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "生成复杂的多表关联SQL"}
    ],
    "lora_adapter_ids": ["sql_adapter"]
  }'

# 请求2: 较小模型 + 代码生成 LoRA（简单代码任务）
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen2.5-72B",
    "messages": [
      {"role": "user", "content": "写一个简单的Hello World程序"}
    ],
    "lora_adapter_ids": ["code_adapter"]
  }'
```

### 场景4: 仅使用基准模型（不使用 LoRA）

适用于：使用原始模型能力，不需要特定任务的微调。

```bash
# 请求: 仅使用基准模型
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "解释什么是机器学习"}
    ]
  }'
```

### 场景5: 使用多个 LoRA 适配器

适用于：需要组合多个微调适配器的能力（如果 vLLM 支持）。

```bash
# 请求: 同时使用多个 LoRA 适配器
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "生成SQL查询并解释代码逻辑"}
    ],
    "lora_adapter_ids": ["sql_adapter", "code_adapter"]
  }'
```

## Python 客户端示例

```python
import requests
import json

API_BASE = "http://localhost:8001"
API_KEY = "sk-your-api-key-here"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# 示例1: SQL 任务
def sql_query(prompt):
    response = requests.post(
        f"{API_BASE}/v1/chat/completions",
        headers=headers,
        json={
            "model": "Qwen3-80B-A3B",
            "messages": [{"role": "user", "content": prompt}],
            "lora_adapter_ids": ["sql_adapter"]
        }
    )
    return response.json()

# 示例2: 代码生成任务
def code_generation(prompt):
    response = requests.post(
        f"{API_BASE}/v1/chat/completions",
        headers=headers,
        json={
            "model": "Qwen3-80B-A3B",
            "messages": [{"role": "user", "content": prompt}],
            "lora_adapter_ids": ["code_adapter"]
        }
    )
    return response.json()

# 示例3: 使用不同模型
def use_different_model(prompt, model_name, lora_id):
    response = requests.post(
        f"{API_BASE}/v1/chat/completions",
        headers=headers,
        json={
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "adapter_id": lora_id
        }
    )
    return response.json()

# 使用示例
if __name__ == "__main__":
    # SQL 查询
    sql_result = sql_query("查询所有用户信息")
    print("SQL结果:", sql_result)
    
    # 代码生成
    code_result = code_generation("写一个Python函数计算阶乘")
    print("代码结果:", code_result)
    
    # 不同模型组合
    custom_result = use_different_model(
        "翻译成英文：你好",
        "Qwen2.5-72B",
        "translation_adapter"
    )
    print("翻译结果:", custom_result)
```

## JavaScript/TypeScript 客户端示例

```typescript
const API_BASE = "http://localhost:8001";
const API_KEY = "sk-your-api-key-here";

interface ChatRequest {
  model: string;
  messages: Array<{ role: string; content: string }>;
  lora_adapter_ids?: string[];
  adapter_id?: string;
}

async function chatCompletion(request: ChatRequest) {
  const response = await fetch(`${API_BASE}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(request)
  });
  return await response.json();
}

// 使用示例
async function examples() {
  // SQL 任务
  const sqlResult = await chatCompletion({
    model: "Qwen3-80B-A3B",
    messages: [{ role: "user", content: "查询所有用户信息" }],
    lora_adapter_ids: ["sql_adapter"]
  });
  
  // 代码生成任务
  const codeResult = await chatCompletion({
    model: "Qwen3-80B-A3B",
    messages: [{ role: "user", content: "写一个Python函数" }],
    lora_adapter_ids: ["code_adapter"]
  });
  
  // 不同模型
  const customResult = await chatCompletion({
    model: "Qwen2.5-72B",
    messages: [{ role: "user", content: "翻译成英文" }],
    adapter_id: "translation_adapter"
  });
}
```

## 流式响应示例

```bash
# 流式请求中使用 LoRA
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-80B-A3B",
    "messages": [
      {"role": "user", "content": "生成SQL查询"}
    ],
    "lora_adapter_ids": ["sql_adapter"],
    "stream": true
  }'
```

## 最佳实践

1. **模型选择**：
   - 复杂任务使用大模型（如 Qwen3-80B-A3B）
   - 简单任务使用较小模型（如 Qwen2.5-72B）以提高效率

2. **LoRA 管理**：
   - 预先加载常用的 LoRA 适配器
   - 为不同任务准备专门的 LoRA 适配器

3. **请求优化**：
   - 对相同模型+LoRA组合的请求进行批处理
   - 使用流式响应处理长文本生成

4. **监控和日志**：
   - 查看日志了解不同模型/LoRA组合的使用情况
   - 监控不同组合的性能指标

5. **错误处理**：
   - 检查模型和 LoRA 是否已加载
   - 处理 vLLM 返回的错误信息

## 常见问题

**Q: 如何知道哪些模型和 LoRA 可用？**

A: 可以通过 `/v1/models` 端点查看 vLLM 报告的可用模型列表（本项目也提供 `/models` 兼容路径）。LoRA 适配器通常需要先加载（预加载或运行时加载）后才能被请求使用。

**Q: 切换模型会影响性能吗？**

A: 是的，切换不同的模型可能需要重新加载模型权重，可能影响响应时间。建议对相同模型的请求进行批处理。

**Q: 可以同时使用多个 LoRA 吗？**

A: 这取决于 vLLM 版本/启动参数的支持情况。可以尝试使用 vLLM 支持的“多 LoRA”请求字段；如果不支持，需要改为单 LoRA 或改用不同部署策略。

**Q: 如何追踪不同模型/LoRA的使用情况？**

A: 查看日志文件中的 `model_lora_combination` 事件，可以了解不同组合的使用频率和性能。
