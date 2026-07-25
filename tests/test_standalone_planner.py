from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from excel_assistant.planners.factory import build_planner
from excel_assistant.planners.llama_cpp import LlamaCppPlanner
from excel_assistant.planners.ollama import InputBudget, OllamaPlanner


class JsonResponse:
    def __init__(self, value: dict) -> None:
        self._data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._data


class StandalonePlannerTests(unittest.TestCase):
    def test_factory_uses_bundled_model_and_server_paths(self):
        config = {
            "planner": {
                "type": "llama_cpp",
                "model_path": "models/planner.gguf",
                "server_path": "runtime/llama/llama-server.exe",
                "context_length": 8192,
                "n_batch": 512,
                "startup_timeout_seconds": 180,
                "request_timeout_seconds": 600,
            }
        }
        with patch(
            "excel_assistant.planners.factory.LlamaCppPlanner",
            return_value=MagicMock(),
        ) as planner_class:
            build_planner(config, Path("C:/bundle"))
        kwargs = planner_class.call_args.kwargs
        self.assertEqual(
            Path(kwargs["model_path"]),
            Path("C:/bundle/models/planner.gguf"),
        )
        self.assertEqual(
            Path(kwargs["server_path"]),
            Path("C:/bundle/runtime/llama/llama-server.exe"),
        )
        self.assertEqual(kwargs["context_length"], 8192)
        self.assertEqual(kwargs["n_batch"], 512)
        self.assertEqual(kwargs["request_timeout_seconds"], 600)

    def test_server_command_is_local_headless_and_non_reasoning(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "models" / "planner.gguf"
            server = root / "runtime" / "llama" / "llama-server.exe"
            model.parent.mkdir(parents=True)
            server.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            server.write_bytes(b"server")
            process = MagicMock()
            process.poll.return_value = 0
            with (
                patch.object(LlamaCppPlanner, "_reserve_port", return_value=43123),
                patch.object(LlamaCppPlanner, "_wait_until_ready"),
                patch(
                    "excel_assistant.planners.llama_cpp.subprocess.Popen",
                    return_value=process,
                ) as popen,
                patch(
                    "excel_assistant.planners.llama_cpp._bind_child_lifetime_to_parent",
                    return_value=None,
                ),
                patch("excel_assistant.planners.llama_cpp.atexit.register"),
            ):
                planner = LlamaCppPlanner(str(model), str(server))
            command = popen.call_args.args[0]
            self.assertIn("--host", command)
            self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
            self.assertIn("--no-ui", command)
            self.assertEqual(command[command.index("--reasoning") + 1], "off")
            self.assertEqual(command[command.index("--reasoning-budget") + 1], "0")
            self.assertEqual(command[command.index("--cache-ram") + 1], "0")
            planner.close()

    def test_openai_response_is_normalized_for_shared_planner_policy(self):
        planner = object.__new__(LlamaCppPlanner)
        OllamaPlanner.__init__(
            planner,
            model="planner.gguf",
            endpoint="http://127.0.0.1:43123",
            context_length=8192,
            max_tokens=700,
            temperature=0.0,
            timeout_seconds=600,
        )
        budget = InputBudget(
            estimated_tokens=100,
            allowed_input_tokens=7246,
            context_length=8192,
            reserved_output_tokens=700,
            safety_tokens=246,
            largest_sections=(),
        )
        response = JsonResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"problem_type":"sorting","steps":[]}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 123},
            }
        )
        payload = {
            "messages": [{"role": "user", "content": "정렬해줘"}],
            "format": {"type": "object"},
            "options": {
                "temperature": 0.0,
                "seed": 0,
                "num_predict": 700,
            },
        }
        with patch(
            "excel_assistant.planners.llama_cpp.urlopen",
            return_value=response,
        ) as mocked_urlopen:
            normalized = planner._chat_payload(payload, budget)
        request = mocked_urlopen.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertTrue(request.full_url.endswith("/v1/chat/completions"))
        self.assertEqual(sent["response_format"]["type"], "json_schema")
        self.assertTrue(
            sent["response_format"]["json_schema"]["strict"]
        )
        self.assertEqual(normalized["prompt_eval_count"], 123)
        self.assertEqual(
            normalized["message"]["content"],
            '{"problem_type":"sorting","steps":[]}',
        )


if __name__ == "__main__":
    unittest.main()
