## 概览

`scripts` 目录包含与运行时运维相关的 **辅助脚本**，本文件只说明每个脚本的职责与输入输出，不包含具体的启动命令或运维流程（这些内容已集中在 `docs` 中）。

如需查看完整的启动/停止/日志管理指引，请参考：

- `docs/START_GUIDE.md`：启动脚本与 Python 启动器使用说明
- `docs/LOG_ROTATION.md`：日志轮转与清理说明

## 文件结构

```text
scripts/
├── start.sh         # 一键启动 vLLM + FastAPI (+ Nginx 可选)
├── stop.sh          # 一键停止所有组件
├── start_vllm.py    # 通过 Python 管理 vLLM 进程
└── log_rotate.sh    # 日志轮转与清理辅助脚本
```

## 脚本职责

### `start.sh`

- 读取 `config/config.yaml` 推导 vLLM / FastAPI 端口等基本信息
- 检查 Python / Conda / Nginx 等运行环境
- 结合 `config/vllm_start_cmd.txt` 或环境变量，决定 vLLM 的启动命令
- **优先** 调用 `start_vllm.py` 以 Python 方式启动 vLLM，失败时回退到纯命令行方式
- 启动 FastAPI（`uvicorn app.main:app`），负责：
  - 日志输出重定向到 `logs/fastapi.log`
  - 将进程 PID 写入 `.pids/fastapi.pid`
- 按需启动 Nginx，并将 PID 写入 `.pids/nginx.pid`
- 启动完成后，对主要端口做健康检查并打印当前服务状态

### `stop.sh`

- 从 `.pids` 目录读取 PID，优先按记录的 PID 停止：
  - FastAPI 进程
  - 通过 `start_vllm.py` 管理的 vLLM 进程（如有）
  - Nginx 主进程（可选）
- 如 PID 文件与实际进程不一致，会回退到按进程名与端口搜索的方式进行停止
- 先尝试优雅停止（`SIGTERM` / `QUIT`），必要时升级为强制停止（`SIGKILL`）
- 停止完成后清理对应的 PID 文件

### `start_vllm.py`

- 通过 `app.config_manager` + `app.vllm_manager.VLLMManager` 调用 vLLM：
  - 启动 vLLM 进程（支持 `python_api` / `cli` 两种模式）
  - 等待 vLLM 通过 `/health` 或 `/v1/models` 就绪
  - 将 vLLM PID 写入 `VLLMConfig.pid_file`（默认 `.pids/vllm.pid`）
- 支持的操作模式：
  - 仅启动 vLLM（可选阻塞等待）
  - 仅停止当前 vLLM（支持 `--force`）
  - 先停后启的重启模式
- 主要作为 `start.sh` 与 `stop.sh` 内部的 **Python 入口**，封装 vLLM 管理的细节。

### `log_rotate.sh`

- 对 `logs/` 下的当前日志文件（如 `fastapi.log` / `vllm.log`）按大小进行轮转：
  - 超过指定阈值（默认 100 MB）时重命名为带时间戳的备份文件
  - 重新创建空日志文件，保持文件描述符可用
- 对历史轮转文件（`*.log.*`）按保留天数进行清理：
  - 统计并输出删除的文件数量与释放的空间
- 脚本逻辑与 `app.log_manager` 的行为保持一致，可视为运行时的 **命令行版工具**。

## 依赖与约定

- 所有脚本默认在 **项目根目录** 运行，并假设以下路径存在或可创建：
  - `config/config.yaml`
  - `logs/`
  - `.pids/`
- 环境相关约定：
  - 默认 Conda 环境名由环境变量 `CONDA_ENV` 控制（脚本中默认值为 `Jeff-py312`）
  - 当未检测到 Conda 时，会回退到系统 Python
- PID 与日志文件路径需与 `AppConfig` / `VLLMConfig` 中的设置保持一致，以避免管理错乱。
