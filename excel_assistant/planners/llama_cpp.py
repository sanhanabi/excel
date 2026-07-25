from __future__ import annotations

import atexit
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .ollama import InputBudget, OllamaPlanner


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _bind_child_lifetime_to_parent(process: subprocess.Popen[bytes]) -> int | None:
    """Kill the server with the app even on crash or forced termination."""

    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _JobExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x00002000
    job_object_extended_limit_information = 9
    assigned = kernel32.SetInformationJobObject(
        job,
        job_object_extended_limit_information,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ) and kernel32.AssignProcessToJobObject(job, process._handle)
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return int(job)


class LlamaCppPlanner(OllamaPlanner):
    """Standalone GGUF planner backed by a bundled llama.cpp server.

    Planner policy is inherited from ``OllamaPlanner``. The bundled server is
    an implementation detail: it listens only on localhost, has no Web UI, and
    is stopped as soon as planning finishes.
    """

    def __init__(
        self,
        model_path: str,
        server_path: str,
        context_length: int = 8192,
        max_tokens: int = 700,
        temperature: float = 0.0,
        n_threads: int | None = None,
        n_batch: int = 512,
        startup_timeout_seconds: int = 180,
        request_timeout_seconds: int = 600,
    ) -> None:
        model = Path(model_path).resolve()
        server = Path(server_path).resolve()
        if not model.is_file():
            raise FileNotFoundError(f"내장 AI 모델 파일을 찾을 수 없습니다: {model}")
        if not server.is_file():
            raise FileNotFoundError(f"내장 AI 실행 파일을 찾을 수 없습니다: {server}")

        self._host = "127.0.0.1"
        self._port = self._reserve_port()
        self._process: subprocess.Popen[bytes] | None = None
        self._job_handle: int | None = None
        endpoint = f"http://{self._host}:{self._port}"
        threads = n_threads or min(max(os.cpu_count() or 4, 4), 8)
        super().__init__(
            model="planner.gguf",
            endpoint=endpoint,
            context_length=context_length,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=request_timeout_seconds,
        )
        command = [
            str(server),
            "--model",
            str(model),
            "--ctx-size",
            str(context_length),
            "--batch-size",
            str(n_batch),
            "--threads",
            str(threads),
            "--parallel",
            "1",
            "--host",
            self._host,
            "--port",
            str(self._port),
            "--no-ui",
            "--reasoning",
            "off",
            "--reasoning-budget",
            "0",
            "--flash-attn",
            "on",
            "--cache-type-k",
            "q8_0",
            "--cache-type-v",
            "q8_0",
            "--cache-ram",
            "0",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            command,
            cwd=server.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._job_handle = _bind_child_lifetime_to_parent(self._process)
        atexit.register(self.close)
        try:
            self._wait_until_ready(startup_timeout_seconds)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_until_ready(self, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        health_url = f"{self._endpoint}/health"
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                raise RuntimeError(
                    "내장 AI를 시작하지 못했습니다. 모델 또는 실행 파일을 다시 받아 주세요."
                )
            try:
                with urlopen(health_url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                pass
            time.sleep(0.2)
        raise RuntimeError(
            "내장 AI를 준비하는 데 시간이 너무 오래 걸렸습니다. "
            "다른 프로그램을 닫고 다시 시도해 주세요."
        )

    def _chat_payload(
        self,
        payload: dict[str, Any],
        input_budget: InputBudget,
    ) -> dict[str, Any]:
        options = dict(payload.get("options") or {})
        schema = payload.get("format") or {"type": "object"}
        request_body = {
            "model": self._model,
            "messages": payload.get("messages") or [],
            "temperature": options.get("temperature", self._temperature),
            "seed": options.get("seed", 0),
            "max_tokens": options.get("num_predict", self._max_tokens),
            "stream": False,
            "cache_prompt": True,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "excel_plan",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        request = Request(
            f"{self._endpoint}/v1/chat/completions",
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except URLError as exc:
            raise RuntimeError(
                "내장 AI가 응답하지 않습니다. 프로그램을 종료한 뒤 다시 실행해 주세요."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("내장 AI 응답을 읽을 수 없습니다.") from exc
        if not isinstance(raw, dict):
            raise ValueError("내장 AI 응답이 JSON 객체가 아닙니다.")

        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("내장 AI가 응답을 만들지 못했습니다.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("내장 AI가 빈 응답을 반환했습니다.")

        normalized: dict[str, Any] = {"message": {"content": content.strip()}}
        usage = raw.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            if isinstance(prompt_tokens, int):
                normalized["prompt_eval_count"] = prompt_tokens
        self._guard_actual_prompt_usage(normalized, input_budget)
        return normalized

    def close(self) -> None:
        process, self._process = self._process, None
        try:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                except OSError:
                    pass
        finally:
            if self._job_handle is not None and os.name == "nt":
                ctypes.windll.kernel32.CloseHandle(self._job_handle)
                self._job_handle = None
