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
import fcntl
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
        extra_env = self.config.sglang.extra_env or {}
        if extra_env:
            logger.debug("sglang_extra_env_loaded", extra_env_keys=list(extra_env.keys()))
        env.update(extra_env)
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

    def _extract_sglang_args(self, command: str) -> List[str]:
        """
        规范化启动命令，提取传递给 sglang 模块的参数。
        会剔除前置的 python/-m/module 等包装，只保留参数部分。
        """
        tokens = shlex.split(command)
        if not tokens:
            return []

        cleaned: List[str] = []
        skip_module = False
        python_tokens = {
            "python",
            "python3",
            "python.exe",
            sys.executable,
        }
        module_tokens = {
            "sglang",
            "sglang.launch_server",
            "sglang.entrypoints.launch_server",
        }

        for tok in tokens:
            if tok in python_tokens:
                continue
            if tok == "-m":
                skip_module = True
                continue
            if skip_module:
                skip_module = False
                # 如果是模块名，跳过
                if tok in module_tokens:
                    continue
                # 如果不是模块名，说明 -m 后面跟的不是模块，可能是参数
                cleaned.append(tok)
                continue
            if tok in module_tokens:
                continue
            cleaned.append(tok)
        return cleaned

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
        """启动 sglang 进程，返回 PID。使用文件锁防止并发启动。"""
        # 第一次检查：快速检查是否已运行
        if self.is_running():
            existing_pid = self._read_pid()
            if existing_pid:
                logger.info("sglang_already_running", pid=existing_pid)
                return existing_pid

        # 使用 PID 文件作为锁文件，防止并发启动
        lock_file_path = self.pid_file + ".lock"
        os.makedirs(os.path.dirname(lock_file_path) or ".", exist_ok=True)
        
        lock_file = None
        try:
            # 创建锁文件
            lock_file = open(lock_file_path, "w")
            try:
                # 尝试获取排他锁（非阻塞）
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                # 如果无法获取锁，说明另一个进程正在启动
                lock_file.close()
                lock_file = None
                # 等待一小段时间，然后再次检查
                time.sleep(0.5)
                if self.is_running():
                    existing_pid = self._read_pid()
                    if existing_pid:
                        logger.info("sglang_started_by_another_process", pid=existing_pid)
                        return existing_pid
                # 如果仍然无法获取锁，抛出异常
                raise RuntimeError("无法获取启动锁，可能有另一个进程正在启动 sglang")
            
            try:
                # 获取锁后，再次检查（双重检查锁定模式）
                if self.is_running():
                    existing_pid = self._read_pid()
                    if existing_pid:
                        logger.info("sglang_already_running_after_lock", pid=existing_pid)
                        return existing_pid
                
                # 现在可以安全地启动
                command = self._load_start_command(override_command)
                env = self._build_env()
                env["PYTHONUNBUFFERED"] = "1"
                self._ensure_log_handle()

                launch_mode = self.config.sglang.launch_mode
                if launch_mode == SGLangLaunchMode.PYTHON_API:
                    # 使用 Python API 方式启动
                    # 提取 sglang 参数，剔除 python/-m/module 等包装
                    sglang_args = self._extract_sglang_args(command)
                    # 构建启动命令
                    launch_cmd = self._get_python_prefix() + [
                        "-m",
                        "sglang.launch_server",
                        *sglang_args,
                    ]
                else:
                    # CLI 模式：直接执行命令
                    launch_cmd = shlex.split(command)

                try:
                    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    
                    # 记录环境变量信息（特别是 extra_env）
                    extra_env_keys = list(self.config.sglang.extra_env.keys()) if self.config.sglang.extra_env else []
                    if extra_env_keys:
                        logger.info(
                            "sglang_starting_with_extra_env",
                            extra_env_keys=extra_env_keys,
                            extra_env_values={k: env.get(k, "***") for k in extra_env_keys}
                        )
                    
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

                    # 写入启动成功信息
                    try:
                        import datetime
                        with open(self.log_file, "a", encoding="utf-8") as f:
                            f.write(f"[{datetime.datetime.now().isoformat()}] sglang 进程已启动，PID: {pid}\n")
                    except Exception:
                        pass
                    
                    # 等待一段时间检查进程是否成功启动
                    # 给进程一些时间来初始化，如果在这段时间内退出，说明启动失败
                    startup_check_delay = 10  # 等待 10 秒
                    for _ in range(startup_check_delay):
                        if self._process.poll() is not None:
                            # 进程已经退出，启动失败
                            exit_code = self._process.returncode
                            self._remove_pid()
                            
                            # 尝试读取最后的错误日志
                            error_summary = ""
                            common_errors = []
                            try:
                                if os.path.exists(self.log_file):
                                    with open(self.log_file, "r", encoding="utf-8", errors="replace") as f:
                                        lines = f.readlines()
                                        # 获取最后 100 行进行分析
                                        recent_lines = lines[-100:]
                                        
                                        # 检查常见错误模式
                                        for line in recent_lines:
                                            line_upper = line.upper()
                                            if "CUDA" in line_upper and ("ERROR" in line_upper or "FAILED" in line_upper):
                                                common_errors.append("检测到 CUDA 相关错误：请检查 CUDA 驱动和 PyTorch 版本兼容性。")
                                            elif "OUT OF MEMORY" in line_upper or "OOM" in line_upper:
                                                common_errors.append("检测到内存不足错误：请减少 GPU 内存使用或使用更小的模型。")
                                            elif "MODEL" in line_upper and ("NOT FOUND" in line_upper or "CANNOT FIND" in line_upper):
                                                common_errors.append("检测到模型路径错误：请检查模型路径是否正确。")
                                            elif "IMPORT" in line_upper and "ERROR" in line_upper:
                                                common_errors.append("检测到导入错误：请检查 sglang 是否正确安装。")
                                        
                                        # 获取最后 50 行作为错误摘要
                                        error_lines = [line for line in recent_lines if "ERROR" in line.upper() or "Traceback" in line or "RuntimeError" in line or "Exception" in line or "ValidationError" in line]
                                        if error_lines:
                                            error_summary = "\n".join(error_lines[-15:])  # 最后 15 个错误行
                            except Exception:
                                pass
                            
                            error_msg = f"sglang 进程在启动后 {startup_check_delay} 秒内退出，退出码: {exit_code}"
                            
                            # 添加常见错误提示
                            if common_errors:
                                error_msg += "\n\n可能的解决方案："
                                for i, hint in enumerate(set(common_errors), 1):  # 使用 set 去重
                                    error_msg += f"\n{i}. {hint}"
                            
                            if error_summary:
                                error_msg += f"\n\n错误详情:\n{error_summary}"
                            
                            try:
                                import datetime
                                with open(self.log_file, "a", encoding="utf-8") as f:
                                    f.write(f"[{datetime.datetime.now().isoformat()}] [ERROR] {error_msg}\n")
                            except Exception:
                                pass
                            
                            logger.error(
                                "sglang_startup_failed",
                                pid=pid,
                                exit_code=exit_code,
                                error_summary=error_summary[:500] if error_summary else None,
                            )
                            raise RuntimeError(error_msg)
                        time.sleep(1)

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
            finally:
                # 释放文件锁
                if lock_file:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except (IOError, OSError):
                        pass
                    lock_file.close()
                    try:
                        os.remove(lock_file_path)
                    except OSError:
                        pass
        except Exception as exc:
            # 如果启动过程中出现异常，确保释放锁
            if lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except (IOError, OSError):
                    pass
                try:
                    lock_file.close()
                except Exception:
                    pass
                try:
                    os.remove(lock_file_path)
                except OSError:
                    pass
            logger.error("sglang_start_exception", error=str(exc))
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

