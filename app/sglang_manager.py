"""sglang 进程管理器：启动/停止/健康检测 sglang 服务。"""

from __future__ import annotations

import os
import shutil
import shlex
import signal
import subprocess
import sys
import time
import threading
from typing import Dict, IO, List, Optional

import httpx

from app.log_manager import rotate_log_file
from app.monitoring import logger
from app.models import AppConfig, SGLangLaunchMode


class SGLangManager:
    """负责启动、健康检测与停止 sglang 进程的管理器。"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.pid_file = config.sglang.pid_file
        self.log_file = config.sglang.log_file
        self._process: Optional[subprocess.Popen] = None
        self._log_fp: Optional[IO[bytes]] = None

        os.makedirs(self.config.sglang.pid_dir, exist_ok=True)
        log_dir = os.path.dirname(self.log_file) or "."
        os.makedirs(log_dir, exist_ok=True)

    def _read_pid(self) -> Optional[int]:
        try:
            with open(self.pid_file, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _write_pid(self, pid: int) -> None:
        with open(self.pid_file, "w", encoding="utf-8") as f:
            f.write(str(pid))

    def _remove_pid(self) -> None:
        try:
            os.remove(self.pid_file)
        except FileNotFoundError:
            pass

    def _load_start_command(self, override_command: Optional[str]) -> str:
        if override_command:
            return override_command.strip()
        if self.config.sglang.start_cmd:
            return self.config.sglang.start_cmd.strip()
        if os.path.exists(self.config.sglang.start_cmd_file):
            with open(self.config.sglang.start_cmd_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        raise RuntimeError("未找到 sglang 启动命令，请设置 config.sglang.start_cmd 或配置文件。")

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env.update(self.config.sglang.extra_env or {})
        return env

    def _get_python_prefix(self) -> List[str]:
        launcher = self.config.sglang.python_launcher
        if launcher.conda_env:
            if shutil.which("conda"):
                return ["conda", "run", "-n", launcher.conda_env, "python"]
            logger.warning(
                "conda_not_found_fallback",
                conda_env=launcher.conda_env,
                fallback=sys.executable,
            )
        return [sys.executable]

    def _ensure_log_handle(self) -> IO[bytes]:
        rotate_log_file(self.log_file, self.config.sglang.log_max_size_mb)
        if self._log_fp and not self._log_fp.closed:
            return self._log_fp
        self._log_fp = open(self.log_file, "ab", buffering=0)
        return self._log_fp

    def _is_pid_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def is_running(self) -> bool:
        if self._process and self._process.poll() is None:
            return True
        pid = self._read_pid()
        if pid is None:
            return False
        alive = self._is_pid_running(pid)
        if not alive:
            self._remove_pid()
        return alive

    def start(self, override_command: Optional[str] = None) -> int:
        """启动 sglang 进程，返回 PID。"""
        if self.is_running():
            existing_pid = self._read_pid()
            if existing_pid:
                logger.info("sglang_already_running", pid=existing_pid)
                return existing_pid

        command = self._load_start_command(override_command)
        env = self._build_env()
        env["PYTHONUNBUFFERED"] = "1"
        self._ensure_log_handle()

        launch_mode = self.config.sglang.launch_mode
        if launch_mode == SGLangLaunchMode.PYTHON_API and command.strip().startswith("python -m "):
            launch_cmd = shlex.split(command)
        elif launch_mode == SGLangLaunchMode.PYTHON_API and command.strip().startswith("-m "):
            launch_cmd = self._get_python_prefix() + shlex.split(command)
        elif launch_mode == SGLangLaunchMode.PYTHON_API and command.strip().startswith("sglang."):
            launch_cmd = self._get_python_prefix() + ["-m"] + shlex.split(command)
        else:
            launch_cmd = shlex.split(command)

        try:
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self._process = subprocess.Popen(
                launch_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=creation_flags,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            pid = self._process.pid
            self._write_pid(pid)

            def log_writer():
                try:
                    with open(self.log_file, "a", encoding="utf-8", buffering=1) as f:
                        if self._process and self._process.stdout:
                            for line in iter(self._process.stdout.readline, ""):
                                if line:
                                    f.write(line)
                except Exception as exc:  # noqa: BLE001
                    logger.error("sglang_log_writer_error", error=str(exc))

            threading.Thread(target=log_writer, daemon=True).start()

            logger.info(
                "sglang_started",
                pid=pid,
                command=" ".join(launch_cmd),
                log_file=self.log_file,
            )
            return pid
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("sglang_start_failed", error=str(exc), command=command)
            raise

    def wait_for_ready(self, host: str, port: int, timeout: int = 60) -> bool:
        """等待 sglang /health 或 /v1/models 就绪。"""
        deadline = time.time() + timeout
        urls = [
            f"http://{host}:{port}/health",
            f"http://{host}:{port}/v1/models",
        ]
        while time.time() < deadline:
            for url in urls:
                try:
                    resp = httpx.get(url, timeout=5.0)
                    if resp.status_code == 200:
                        logger.info("sglang_ready", url=url)
                        return True
                except httpx.HTTPError:
                    pass
            time.sleep(2)
        logger.warning("sglang_ready_timeout", host=host, port=port, timeout=timeout)
        return False

    def stop(self, force: bool = False) -> None:
        """停止 sglang 进程。"""
        pid = self._read_pid()

        def _kill(target_pid: int) -> None:
            try:
                kill_signal = signal.SIGTERM
                if force:
                    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(target_pid, kill_signal)
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.warning("sglang_stop_error", error=str(exc), pid=target_pid)

        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                if force:
                    time.sleep(1)
                    if self._process.poll() is None:
                        self._process.kill()
            except OSError as exc:
                logger.warning("sglang_stop_error", error=str(exc))
        elif pid:
            _kill(pid)

        self._remove_pid()
        if self._log_fp and not self._log_fp.closed:
            try:
                self._log_fp.flush()
                self._log_fp.close()
            except (OSError, ValueError):
                pass

