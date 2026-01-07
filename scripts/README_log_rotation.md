# 日志管理说明

## 功能概述

系统提供了自动日志轮转和清理功能，确保日志文件不会无限增长：

1. **日志轮转**：当日志文件超过100MB时自动轮转
2. **自动清理**：自动删除7天前的旧日志文件
3. **后台任务**：应用启动后每24小时自动检查一次

## 日志文件结构

```
logs/
├── fastapi.log              # 当前FastAPI日志（正在写入）
├── fastapi.log.20241225_143000  # 轮转后的日志文件（带时间戳）
├── vllm.log                 # 当前vLLM日志（正在写入）
└── vllm.log.20241225_143000     # 轮转后的日志文件（带时间戳）
```

## 自动管理

### 1. 启动时自动清理
- 应用启动时会自动清理7天前的旧日志
- 启动脚本会在启动前检查并轮转大文件（>100MB）

### 2. 后台定时任务
- 应用启动后，每24小时自动检查一次
- 自动轮转大文件
- 自动清理旧日志

## 手动管理

### 使用日志轮转脚本

```bash
# 基本用法（使用默认参数）
./scripts/log_rotate.sh

# 指定参数
./scripts/log_rotate.sh [日志目录] [最大文件大小MB] [保留天数]
./scripts/log_rotate.sh logs 100 7
```

### 使用API接口（需要管理员权限）

```bash
# 清理日志（默认保留7天）
curl -X POST http://localhost:8001/admin/clean-logs \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"days": 7}'

# 获取日志统计信息
curl -X GET http://localhost:8001/admin/log-stats \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

## 配置定时任务（可选）

如果需要更频繁的清理，可以设置crontab：

```bash
# 编辑crontab
crontab -e

# 添加以下行（每天凌晨2点执行日志清理）
0 2 * * * /root/sj-tmp/Jeff/LLMHOST/scripts/log_rotate.sh /root/sj-tmp/Jeff/LLMHOST/logs 100 7 >> /root/sj-tmp/Jeff/LLMHOST/logs/log_rotate.log 2>&1
```

## 配置说明

### 默认配置
- **最大文件大小**：100MB
- **保留天数**：7天
- **检查间隔**：24小时

### 修改配置

可以通过修改以下文件来调整配置：

1. **应用配置** (`app/main.py`):
   - `max_size_mb=100.0` - 最大文件大小
   - `days_to_keep=7` - 保留天数
   - `check_interval_hours=24` - 检查间隔

2. **启动脚本** (`scripts/start.sh`):
   - 修改 `max_size=$((100 * 1024 * 1024))` - 最大文件大小（字节）

3. **轮转脚本** (`scripts/log_rotate.sh`):
   - 修改默认参数：`MAX_SIZE_MB="${2:-100}"` 和 `DAYS_TO_KEEP="${3:-7}"`

## 注意事项

1. **当前日志文件不会被删除**：只有轮转后的文件（带时间戳）会被清理
2. **轮转不会中断服务**：轮转时会重命名文件，然后创建新文件，服务继续写入新文件
3. **磁盘空间监控**：建议定期检查日志目录的磁盘使用情况

## 故障排查

### 日志文件过大
```bash
# 手动轮转
./scripts/log_rotate.sh logs 100 7

# 或直接重命名
mv logs/fastapi.log logs/fastapi.log.$(date +%Y%m%d_%H%M%S)
touch logs/fastapi.log
```

### 清理不生效
1. 检查日志目录权限
2. 检查脚本执行权限：`chmod +x scripts/log_rotate.sh`
3. 查看应用日志中的错误信息

