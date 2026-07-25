# ExcelAssistant v1.0.0

Windows용 완전 오프라인 스탠드얼론 배포판입니다.

- Python 설치 불필요
- Ollama 설치 불필요
- 인터넷 연결과 API 키 불필요
- Qwen3.5 4B GGUF 모델 및 llama.cpp 런타임 포함
- 원본 Excel 파일을 수정하지 않고 별도의 결과 파일 생성
- 최소 지원 메모리: 16GB RAM
- Python 3.9.9 및 제3자 구성요소 라이선스 본문 포함

## 설치

아래 세 파일을 같은 폴더에 다운로드합니다.

1. `ExcelAssistant-v1.0.0-win64.zip.001`
2. `ExcelAssistant-v1.0.0-win64.zip.002`
3. `Install_ExcelAssistant.cmd`

`Install_ExcelAssistant.cmd`를 더블클릭하면 각 파일의 SHA-256을 먼저
확인합니다. 두 조각을 합친 뒤 완성된 ZIP도 다시 확인하고
`ExcelAssistant-v1.0.0` 폴더에 압축을 풉니다. 생성된 폴더의
`ExcelAssistant.exe`를 실행하면 됩니다.

`SHA256SUMS.txt`는 다운로드 파일의 무결성을 직접 확인하려는 사용자를
위한 선택적 체크섬 파일입니다.

이 배포판은 코드 서명되지 않았으므로 Windows SmartScreen이나 회사 보안
프로그램에서 경고할 수 있습니다. 회사 PC에서는 조직의 보안 정책을
따르세요.

---

This is the complete offline Windows standalone distribution.

- No Python or Ollama installation
- No internet connection or API key
- Bundled Qwen3.5 4B GGUF model and llama.cpp runtime
- Source workbooks are never overwritten
- Minimum supported memory: 16GB RAM
- Python 3.9.9 and third-party license texts included

Download the two archive parts and `Install_ExcelAssistant.cmd` into the same
folder, then run the CMD file. Using built-in Windows commands, it verifies
both parts, joins them, verifies the completed archive, and then extracts the
package.

The executable is not code-signed and may trigger Windows SmartScreen or
company security software. Follow the target organization's security policy.

The ExcelAssistant source code is licensed under MIT. Bundled third-party
components retain their respective upstream licenses.
