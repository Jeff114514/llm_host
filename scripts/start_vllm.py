#!/usr/bin/env python3
"""使用 Python 启动 vLLM OpenAI Server."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

# 将项目根目录添加到 Python 路径中
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    from app.config_manager import init_config
    from app.vllm_manager import VLLMManager
except ModuleNotFoundError as exc:
    # 兜底：某些运行方式（如被拷贝到其它目录、或通过 conda run/容器入口执行）
    # 可能导致项目根目录未被正确加入 sys.path。
    if exc.name != "app":
        raise
    if str(PROJECT_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECT_DIR))
    from app.config_manager import init_config
    from app.vllm_manager import VLLMManager


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 vLLM Python 进程")
    parser.add_argument(
        "--config-file",
        help="自定义配置文件路径（默认读取 config/config.yaml）",
    )
    parser.add_argument(
        "--command",
        help="覆盖配置文件中的 vLLM 启动命令",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="启动后阻塞等待，直到用户中断或进程退出",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="仅停止当前正在运行的 vLLM 进程",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="先停止再重新启动 vLLM",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="停止进程时使用强制方式",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="等待 vLLM /health 就绪的超时时间（秒）",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.config_file:
        os.environ["CONFIG_FILE"] = args.config_file

    config = init_config()
    manager = VLLMManager(config)

    def _handle_signal(signum, _frame):
        manager.stop(force=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    if args.stop:
        if manager.is_running():
            manager.stop(force=args.force)
            print("[vLLM] 已停止正在运行的进程")  # noqa: T201
        else:
            print("[vLLM] 当前没有运行中的进程")  # noqa: T201
        return

    if args.restart:
        manager.stop(force=args.force)
        time.sleep(1)

    pid = manager.start(override_command=args.command)
    ready = manager.wait_for_ready(
        config.vllm_host,
        config.vllm_port,
        timeout=args.timeout,
    )

    print(f"[vLLM] 进程 PID: {pid}, ready={ready}")  # noqa: T201

    if args.wait:
        try:
            while manager.is_running():
                time.sleep(2)
        except KeyboardInterrupt:
            manager.stop()


if __name__ == "__main__":
    main()
