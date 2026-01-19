# 启动脚本使用指南

本文档详细介绍启动和停止脚本的使用方法。

## 脚本文件

- `scripts/start.sh` - 启动脚本
- `scripts/stop.sh` - 停止脚本
- `scripts/start_vllm.py` - vLLM 启动/停止/重启工具（读取 `config/config.yaml`）
- `scripts/log_rotate.sh` - 日志轮转与清理脚本（Bash）

## start.sh - 启动脚本

启动脚本用于启动所有服务（vLLM、FastAPI、Nginx）。

### 功能

- 自动检测Conda环境
- 启动vLLM服务（使用Conda环境）
- 启动FastAPI服务
- 可选启动Nginx
- 健康检查和服务就绪验证
- PID文件管理

### 使用方法

```bash
# 基本使用（使用配置文件中的vLLM命令）
./scripts/start.sh

# 指定vLLM启动命令
./scripts/start.sh 'python -m vllm.entrypoints.openai.api_server --model your-model --port 8002'

# 使用环境变量指定vLLM命令
export VLLM_START_CMD='your vllm command'
./scripts/start.sh
```

### 工作流程

1. 检查Python环境和依赖
2. 加载配置文件（`config/config.yaml`）
3. 获取vLLM启动命令（优先级：参数 > 环境变量 > 配置文件 > 默认命令）
4. 检查端口占用
5. 启动vLLM服务（后台运行）
6. 等待vLLM就绪
7. 启动FastAPI服务（后台运行）
8. 等待FastAPI就绪
9. 可选启动Nginx
10. 显示服务状态

### 输出信息

- 服务启动状态
- PID信息
- 日志文件位置
- 访问地址

### 日志文件

- vLLM: `logs/vllm.log`
- FastAPI: `logs/fastapi.log`

### PID文件

- vLLM: `.pids/vllm.pid`
- FastAPI: `.pids/fastapi.pid`
- Nginx: `.pids/nginx.pid`

## stop.sh - 停止脚本

停止脚本用于停止所有服务。

### 功能

- 停止FastAPI服务
- 停止vLLM服务
- 可选停止Nginx
- 清理PID文件

### 使用方法

```bash
./scripts/stop.sh
```

### 工作流程

1. 停止FastAPI服务（通过PID文件）
2. 停止vLLM服务（通过PID文件）
3. 询问是否停止Nginx
4. 如果选择停止，先尝试通过PID文件停止
5. 如果PID文件方式失败，通过进程查找停止
6. 清理PID文件

### 停止方式

- 优先使用PID文件
- 如果PID文件不存在或进程不存在，通过进程查找
- 优雅停止（SIGTERM）
- 如果仍在运行，强制停止（SIGKILL）

## start_vllm.py - Python启动器

Python启动器用于启动、停止和重启vLLM服务。

### 使用方法

```bash
# 启动vLLM并等待就绪
python scripts/start_vllm.py --wait

# 仅启动vLLM（不等待）
python scripts/start_vllm.py

# 停止vLLM
python scripts/start_vllm.py --stop

# 强制停止vLLM
python scripts/start_vllm.py --stop --force

# 重启vLLM
python scripts/start_vllm.py --restart

# 使用自定义配置文件
python scripts/start_vllm.py --config-file /path/to/config.yaml

# 覆盖启动命令
python scripts/start_vllm.py --command "your vllm command"
```

### 参数说明

- `--config-file`: 自定义配置文件路径（默认读取 `config/config.yaml`）
- `--command`: 覆盖配置文件中的 vLLM 启动命令
- `--wait`: 启动后阻塞等待，直到用户中断或进程退出
- `--stop`: 仅停止当前正在运行的 vLLM 进程
- `--restart`: 先停止再重新启动 vLLM
- `--force`: 停止进程时使用强制方式
- `--timeout`: 等待 vLLM /health 就绪的超时时间（秒，默认60）

## 环境要求

### Conda环境

脚本会自动检测Conda环境（默认：Jeff-py312），可以通过环境变量修改：

```bash
export CONDA_ENV=your-env-name
./scripts/start.sh
```

### 依赖检查

启动脚本会检查：
- Python3是否安装
- Conda是否可用（可选）
- Nginx是否安装（可选）

## 配置依赖

脚本依赖以下配置文件：

- `config/config.yaml`: 主配置文件
- `config/vllm_start_cmd.txt`: vLLM启动命令（可选）
- `nginx/nginx.conf`: Nginx配置文件

## 使用示例

### 完整启动流程

```bash
# 1. 进入项目目录
cd /root/sj-tmp/Jeff/LLMHOST

# 2. 激活Conda环境（可选，脚本会自动检测）
conda activate Jeff-py312

# 3. 启动所有服务
./scripts/start.sh

# 4. 检查服务状态
curl http://localhost:8001/health
curl http://localhost:8000/health  # 通过Nginx
```

### 仅启动vLLM和FastAPI

```bash
# 启动服务
./scripts/start.sh

# 当询问是否启动Nginx时，选择N（不启动）
```

### 自定义vLLM启动命令

```bash
# 方式1: 通过参数传递
./scripts/start.sh 'conda run -n Jeff-py312 python -m vllm.entrypoints.openai.api_server --model /path/to/model --port 8002'

# 方式2: 通过环境变量
export VLLM_START_CMD='your command'
./scripts/start.sh

# 方式3: 修改配置文件
echo 'your command' > config/vllm_start_cmd.txt
./scripts/start.sh
```

### 停止服务

```bash
# 停止所有服务
./scripts/stop.sh

# 当询问是否停止Nginx时，选择y（停止）或N（不停止）
```

## 故障排查

### 服务启动失败

1. **检查端口占用**:
```bash
lsof -i :8000  # Nginx
lsof -i :8001  # FastAPI
lsof -i :8002  # vLLM
```

2. **查看日志**:
```bash
tail -f logs/vllm.log
tail -f logs/fastapi.log
```

3. **检查Conda环境**:
```bash
conda activate Jeff-py312
python --version
```

4. **检查配置文件**:
```bash
cat config/config.yaml
```

### PID文件问题

如果PID文件存在但进程不存在：

```bash
# 清理PID文件
rm -f .pids/*.pid

# 重新启动
./scripts/start.sh
```

### Nginx启动失败

1. **检查配置文件**:
```bash
nginx -t -c /root/sj-tmp/Jeff/LLMHOST/nginx/nginx.conf
```

2. **检查端口占用**:
```bash
lsof -i :8000
```

3. **查看Nginx错误日志**:
```bash
tail -f /var/log/nginx/vllm_proxy_error.log
```

### 服务无法停止

如果正常停止失败：

```bash
# 手动查找并停止进程
ps aux | grep vllm
ps aux | grep uvicorn
ps aux | grep nginx

# 强制停止
kill -9 <PID>
```

## 高级用法

### 后台运行

脚本已经使用nohup在后台运行服务，如果需要完全后台运行脚本本身：

```bash
nohup ./scripts/start.sh > start.log 2>&1 &
```

### 定时重启

可以设置cron任务定期重启服务：

```bash
# 编辑crontab
crontab -e

# 添加任务（每天凌晨3点重启）
0 3 * * * /root/sj-tmp/Jeff/LLMHOST/scripts/stop.sh && /root/sj-tmp/Jeff/LLMHOST/scripts/start.sh
```

### 监控脚本

可以创建监控脚本检查服务状态：

```bash
#!/bin/bash
# check_services.sh

if ! curl -s http://localhost:8001/health > /dev/null; then
    echo "FastAPI服务异常，正在重启..."
    cd /root/sj-tmp/Jeff/LLMHOST
    ./scripts/stop.sh
    ./scripts/start.sh
fi
```

## 注意事项

1. **权限问题**: 确保脚本有执行权限：`chmod +x scripts/*.sh`
2. **路径问题**: 脚本会自动检测项目目录，确保在项目根目录运行
3. **端口冲突**: 确保配置的端口未被占用
4. **资源限制**: 根据服务器资源调整vLLM和FastAPI的配置
5. **日志管理**: 定期清理日志文件，避免磁盘空间不足
