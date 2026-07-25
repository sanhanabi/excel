# Building ExcelAssistant

This document describes the Windows x64 environment used for the `v1.0.0`
standalone build. The large model and llama.cpp binaries are intentionally not
stored in Git.

[한국어 빌드 안내](BUILDING.ko.md)

## Reference environment

- Windows 10/11 x64
- CPython 3.9.9 x64
- Nuitka 2.8.4
- pandas 2.3.3
- openpyxl 3.1.5
- llama.cpp build 9637 (`aedb2a5e9`)
- Qwen3.5-4B GGUF, Q4_K_M quantization

The exact Python package versions are pinned in
[`requirements-build.txt`](requirements-build.txt).

## 1. Prepare Python

From the repository root in PowerShell:

```powershell
py -3.9 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install --upgrade pip
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
```

## 2. Add the model

Download the Q4_K_M GGUF from the
[Qwen3.5-4B GGUF repository](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF)
and place it at:

```text
models/planner.gguf
```

The model bundled with `v1.0.0` has:

```text
Size:   3,013,027,808 bytes
SHA256: 13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983
```

Verify it in PowerShell:

```powershell
Get-FileHash .\models\planner.gguf -Algorithm SHA256
```

Do not silently substitute another quantization. A different model or
quantization can change planning behavior and requires its own QA baseline.

## 3. Add llama.cpp

Use an official Windows x64
[llama.cpp release](https://github.com/ggml-org/llama.cpp/releases) compatible
with build 9637 and extract the server executable and its adjacent DLLs into:

```text
runtime/llama/
├── llama-server.exe
└── required DLL files
```

The reference server reports:

```text
version: 9637 (aedb2a5e9)
```

Check it with:

```powershell
.\runtime\llama\llama-server.exe --version
```

## 4. Run the deterministic tests

The unit suite does not download or execute the 3 GB model:

```powershell
.\.venv-build\Scripts\python.exe -m unittest discover -s tests -v
```

To check only that the bundled inference runtime starts and stops:

```powershell
.\.venv-build\Scripts\python.exe scripts\smoke_standalone_runtime.py `
  --model models\planner.gguf `
  --server runtime\llama\llama-server.exe
```

## 5. Build the standalone folder

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_standalone.ps1
```

The script creates a timestamped folder under `dist/`. It contains
`ExcelAssistant.exe`, the Python runtime, `config.json`, the model, llama.cpp,
and the applicable license files.

Nuitka may download its compiler toolchain into the configured local cache on
the first build. Compiler and Windows SDK differences can prevent a
byte-for-byte identical executable even when the application inputs match.

## Runtime privacy default

The checked-in `config.json` disables plan logging. Developers who explicitly
need diagnostic logs can set `logging.enabled` to `true`. Such logs can contain
the user's request and selected plan, so they must not be collected or shared
without reviewing their contents.
