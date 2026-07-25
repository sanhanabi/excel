# Third-party notices

The source repository does not include model weights or third-party inference
runtimes. The Windows standalone distribution bundles them under their
respective licenses.

## Qwen3.5-4B

- Project: <https://huggingface.co/Qwen/Qwen3.5-4B>
- License: Apache License 2.0
- Full license text: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt)

GGUF quantizations may be obtained separately from:

- <https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF>

## llama.cpp

- Project: <https://github.com/ggml-org/llama.cpp>
- License: MIT License
- Full license text: [`licenses/llama.cpp-MIT.txt`](licenses/llama.cpp-MIT.txt)

## Python dependencies

Python dependencies are declared in `requirements.txt` and retain their
respective upstream copyright notices and licenses.

The standalone distribution also contains the Python runtime. Python is
distributed under the Python Software Foundation License.

The standalone build copies available license and notice files for bundled
Python packages from the build environment into
`licenses/python-packages/`.
