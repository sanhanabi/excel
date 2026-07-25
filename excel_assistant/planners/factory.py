from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Planner
from .llama_cpp import LlamaCppPlanner
from .ollama import OllamaPlanner
from .rule_based import RuleBasedPlanner


def build_planner(config: dict[str, Any], base_dir: Path) -> Planner:
    planner_config = dict(config.get("planner") or {})
    planner_type = planner_config.get("type", "rule_based")
    if planner_type == "rule_based":
        return RuleBasedPlanner()
    if planner_type == "ollama":
        return OllamaPlanner(
            model=str(planner_config.get("model", "qwen3.5:4b-q4_K_M")),
            endpoint=str(planner_config.get("endpoint", "http://127.0.0.1:11434")),
            context_length=int(planner_config.get("context_length", 8192)),
            max_tokens=int(planner_config.get("max_tokens", 700)),
            temperature=float(planner_config.get("temperature", 0.0)),
            timeout_seconds=int(planner_config.get("timeout_seconds", 180)),
        )
    if planner_type == "llama_cpp":
        raw_path = Path(str(planner_config.get("model_path", "models/planner.gguf")))
        model_path = raw_path if raw_path.is_absolute() else base_dir / raw_path
        raw_server_path = Path(
            str(
                planner_config.get(
                    "server_path",
                    "runtime/llama/llama-server.exe",
                )
            )
        )
        server_path = (
            raw_server_path
            if raw_server_path.is_absolute()
            else base_dir / raw_server_path
        )
        raw_threads = planner_config.get("n_threads")
        return LlamaCppPlanner(
            model_path=str(model_path),
            server_path=str(server_path),
            context_length=int(planner_config.get("context_length", 8192)),
            max_tokens=int(planner_config.get("max_tokens", 700)),
            temperature=float(planner_config.get("temperature", 0.0)),
            n_threads=(None if raw_threads is None else int(raw_threads)),
            n_batch=int(planner_config.get("n_batch", 512)),
            startup_timeout_seconds=int(
                planner_config.get("startup_timeout_seconds", 180)
            ),
            request_timeout_seconds=int(
                planner_config.get("request_timeout_seconds", 600)
            ),
        )
    raise ValueError(f"지원하지 않는 Planner 종류입니다: {planner_type}")
