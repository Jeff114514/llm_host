## 概览

本文件简要说明 **日志轮转相关脚本与代码的职责**。  
运行方式与运维细节请参考：

- `docs/LOG_ROTATION.md`：日志轮转与清理的完整说明（推荐）

## 相关组件

### `scripts/log_rotate.sh`

Shell 脚本，用于：

- 对 `logs/*.log` 按大小进行轮转（重命名为带时间戳的备份文件）
- 清理超过保留天数的轮转日志（`*.log.*`）
- 既可按需手动执行，也可结合 crontab 等方式定期运行

### `app/log_manager.py`

Python 版日志管理工具，与脚本逻辑保持一致：

- `rotate_log_file()`：单个日志文件按大小轮转
- `clean_old_logs()`：按天清理历史轮转文件
- `get_log_stats()`：统计日志数量与空间占用
- `setup_log_rotation()`：创建协程任务，周期性轮转与清理

### `app/main.py`

在应用生命周期中集成日志管理逻辑：

- 启动时执行一次旧日志清理和大文件轮转
- 启动后台定时任务，定期调用 `setup_log_rotation()`

## 日志目录约定

- 默认日志目录为 `logs/`，主要文件包括：
  - `logs/fastapi.log`：FastAPI 结构化日志
  - `logs/vllm.log`：vLLM 进程输出
- 轮转后会生成形如 `*.log.YYYYMMDD_HHMMSS` 的备份文件，例如：

```text
logs/
├── fastapi.log
├── fastapi.log.20241225_143000
├── vllm.log
└── vllm.log.20241225_143000
```
