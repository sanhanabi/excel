# ExcelAssistant 빌드 방법

이 문서는 `v1.0.0` Windows x64 스탠드얼론 배포판을 만들 때 사용한
환경을 설명합니다. 용량이 큰 모델과 llama.cpp 바이너리는 Git에 포함하지
않습니다.

[English build guide](BUILDING.md)

## 기준 환경

- Windows 10/11 x64
- CPython 3.9.9 x64
- Nuitka 2.8.4
- pandas 2.3.3
- openpyxl 3.1.5
- llama.cpp 빌드 9637 (`aedb2a5e9`)
- Qwen3.5-4B GGUF, Q4_K_M 양자화

Python 패키지의 정확한 버전은
[`requirements-build.txt`](requirements-build.txt)에 고정되어 있습니다.

## 1. Python 빌드 환경 준비

저장소 루트에서 PowerShell로 실행합니다.

```powershell
py -3.9 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install --upgrade pip
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
```

## 2. 모델 배치

[Qwen3.5-4B GGUF 저장소](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF)에서
Q4_K_M GGUF를 받은 뒤 다음 경로에 둡니다.

```text
models/planner.gguf
```

`v1.0.0`에 포함한 모델 정보는 다음과 같습니다.

```text
크기:   3,013,027,808바이트
SHA256: 13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983
```

PowerShell 검증 명령:

```powershell
Get-FileHash .\models\planner.gguf -Algorithm SHA256
```

다른 양자화 파일을 같은 이름으로 조용히 교체하면 안 됩니다. 모델이나
양자화가 달라지면 계획 결과도 달라질 수 있으므로 별도의 QA 기준선이
필요합니다.

## 3. llama.cpp 배치

공식 [llama.cpp Release](https://github.com/ggml-org/llama.cpp/releases)에서
빌드 9637과 호환되는 Windows x64 파일을 받아 서버와 함께 제공되는 DLL을
다음 폴더에 풉니다.

```text
runtime/llama/
├── llama-server.exe
└── 필요한 DLL 파일
```

기준 서버의 버전 출력은 다음과 같습니다.

```text
version: 9637 (aedb2a5e9)
```

확인 명령:

```powershell
.\runtime\llama\llama-server.exe --version
```

## 4. 결정론적 테스트 실행

단위 테스트는 3GB 모델을 다운로드하거나 실행하지 않습니다.

```powershell
.\.venv-build\Scripts\python.exe -m unittest discover -s tests -v
```

내장 추론 서버가 시작되고 종료되는지만 확인하려면 다음을 실행합니다.

```powershell
.\.venv-build\Scripts\python.exe scripts\smoke_standalone_runtime.py `
  --model models\planner.gguf `
  --server runtime\llama\llama-server.exe
```

## 5. 스탠드얼론 폴더 빌드

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_standalone.ps1
```

`dist/` 아래에 시각이 포함된 새 폴더가 만들어집니다. 폴더에는
`ExcelAssistant.exe`, Python 런타임, `config.json`, 모델, llama.cpp와
각 라이선스 파일이 들어갑니다.

Nuitka는 첫 빌드에서 컴파일러 도구를 로컬 캐시에 받을 수 있습니다.
동일한 입력을 사용해도 컴파일러나 Windows SDK가 다르면 실행 파일이
바이트 단위로 완전히 같지는 않을 수 있습니다.

## 실행 로그 개인정보 기본값

저장소의 기본 `config.json`은 계획 로그를 비활성화합니다. 개발자가 진단
로그가 꼭 필요한 경우에만 `logging.enabled`를 `true`로 바꿀 수 있습니다.
로그에는 사용자 요청과 선택된 계획이 들어갈 수 있으므로 내용을 확인하지
않고 수집하거나 공유하면 안 됩니다.
