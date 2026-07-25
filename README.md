# ExcelAssistant

> A self-contained Windows Excel assistant powered by a bundled 4B local model.

[한국어 README](README.ko.md)

ExcelAssistant is a completed Windows standalone application that turns a natural-language request into a restricted Excel operation plan. The distribution already contains the Python runtime, a quantized Qwen3.5 4B model, and the llama.cpp runtime.

For an end user, setup is:

```text
Download the package → extract it → run ExcelAssistant.exe
```

Python, Ollama, a separate model installation, an API key, and an internet connection are not required.

## Download the standalone release

The complete offline package includes the 4B model and is larger than
GitHub's per-file release limit, so `v1.0.0` is provided as two archive parts.

1. Open [ExcelAssistant v1.0.0](https://github.com/sanhanabi/excel/releases/tag/v1.0.0).
2. Download these three files into the same folder:
   - `ExcelAssistant-v1.0.0-win64.zip.001`
   - `ExcelAssistant-v1.0.0-win64.zip.002`
   - `Install_ExcelAssistant.cmd`
3. Double-click `Install_ExcelAssistant.cmd`.
4. Open the generated `ExcelAssistant-v1.0.0` folder and run
   `ExcelAssistant.exe`.

The installer only joins and extracts the two archive parts with built-in
Windows commands. It does not install Python, Ollama, 7-Zip, or a model
server.

## Why this project exists

People often know the result they need from a workbook without knowing the
Excel functions, filters, pivot-table steps, or VBA required to produce it.
Conventional automation does not fully solve that gap:

- A fixed macro is efficient only while the workbook layout and column names
  remain as expected.
- Free-form AI code generation can invent unsafe or invalid operations.
- A cloud AI service may be unavailable when company data cannot leave the
  local PC.
- Installing Python, model servers, and development tools is often prohibited
  or impractical on a managed office computer.

ExcelAssistant was built to separate language understanding from execution.
The local model interprets the user's problem and proposes a restricted plan;
ordinary code validates and executes only registered operations.

## How it differs from typical Excel automation

| Approach | Common limitation | ExcelAssistant |
|---|---|---|
| Manual Excel work | Requires knowledge of formulas, filters, and pivots | Accepts an ordinary-language request |
| VBA or a fixed macro | Often depends on a known workbook layout | Profiles the selected workbook and connects the request to actual columns |
| Free-form AI code generation | May create or run arbitrary code | The model can choose only registered operations |
| Cloud AI assistance | May require workbook data to leave the PC | Planning and execution remain local and offline |
| Fixed-purpose automation | Supports only predefined workflows | Composes multiple validated operations into an ordered plan |
| Unreviewed AI execution | Can hide a wrong interpretation until after execution | Shows the plan, columns, conditions, and expected result before approval |

The defining idea is:

> ExcelAssistant does not let AI operate Excel directly. A local model
> translates the user's problem into a restricted plan, and validated code
> executes only that plan.

## The design premise

This project starts from the assumption that a small local model cannot be trusted to execute arbitrary operations.

The model does not write or run Python code. It may only propose a plan using 48 registered Excel operations. The application then checks the function names, columns, parameters, ordering, and executability before showing the plan to the user.

```text
Excel workbook
      ↓
Primary table detection
      ↓
Local 4B model proposes a restricted plan
      ↓
Contract and executability validation
      ↓
Preview on an in-memory copy
      ↓
User confirmation
      ↓
Deterministic pandas/openpyxl execution
      ↓
New result workbook
```

The validator does not claim to know whether the selected table or business meaning matches the user's true intent. That semantic decision remains with the user at the confirmation step.

## Why 16GB RAM and a 4B model

The initial target was an 8GB system with a 2B model. Actual testing showed that the 2B model could not reliably understand even basic Excel vocabulary and task intent.

The supported baseline was therefore changed to:

- Minimum memory: 16GB RAM
- Bundled model: quantized Qwen3.5 4B
- Dedicated GPU: not required
- Operating system: Windows 10/11
- Network: not required

This is a measured minimum target, not an aspirational low-spec claim.

## Using the standalone application

1. Extract the complete package.
2. Run `ExcelAssistant.exe`.
3. Select one or more `.xlsx` or `.xlsm` files.
4. Confirm the automatically detected primary table.
5. Enter one clear task or a sequence of ordered tasks.
6. Select **Review task**.
7. Check the proposed columns, conditions, steps, and expected row count.
8. Approve the plan and choose a new output filename.

The first plan may take several minutes while the bundled model starts.

Requests are most reliable when they use actual column names and cell values:

```text
Group by branch and sum Amount, sort from largest to smallest,
then add a grand-total row.
```

## Original-file protection

- The input workbook is never saved or overwritten.
- A result path cannot be the same as any source path.
- Planning and preview run against an in-memory copy.
- The result is written to a newly created workbook.
- Source-file bytes are compared before and after execution in automated tests.
- A failure during planning or execution does not modify the original workbook.

Original data, validation copies, and result data are deliberately kept separate because the system is designed around distrust of model output.

## Supported operations

The current catalog contains 48 registered operations covering:

- Primary-table detection
- Multiple file and sheet combination
- Blank, duplicate, missing-value, and text cleanup
- Column selection, deletion, ordering, and renaming
- Numeric, date, text, and Boolean conversion
- Single and nested AND/OR filtering
- Relative-date filtering and latest-record selection
- Grouped sums, averages, counts, and multi-aggregation
- Pivoting, ranking, top/bottom N, cumulative totals, and growth rates
- Arithmetic and date-difference columns
- Required-value, error, and duplicate markers
- Number formats, row/cell highlighting, and color scales
- Subtotals, grand totals, and conditional summary formulas
- Optional hidden-row handling

Every operation has the form `DataFrame + validated parameters → new DataFrame`.

## Standalone runtime

The release layout contains:

```text
ExcelAssistant.exe
config.json
models/
└── planner.gguf
runtime/
└── llama/
    ├── llama-server.exe
    └── required llama.cpp DLLs
Python runtime and application dependencies
LICENSE
THIRD_PARTY_NOTICES.md
licenses/
```

The application starts `llama-server.exe` on a temporary localhost port with its Web UI disabled. It is used only as the internal inference engine and is stopped when planning finishes. A Windows Job Object also prevents the server from remaining in memory if the application is terminated unexpectedly.

## Source code

This repository contains the application source and tests for portfolio review and technical evaluation. Large distribution artifacts are intentionally not committed:

- The 2.87GB GGUF model
- Compiled executable and Python runtime
- llama.cpp binaries
- Test workbooks and generated results
- Local plan logs

The standalone package, rather than a source checkout, is the end-user application.
The default [`config.json`](config.json) mirrors the standalone layout and uses the bundled llama.cpp server, not Ollama.

## Verification

```powershell
py -m unittest discover -s tests -v
```

143 automated tests currently pass. They cover table detection, source immutability, grounded filtering, nested conditions, aggregation, formulas, formatting, model context limits, retry behavior, and the bundled llama.cpp transport.

## Limitations

- The result is a newly generated table, not an in-place edit of the original workbook.
- Existing charts, pivot tables, shapes, macros, and full workbook layout are not copied.
- Sheets with one primary data table are a better fit than report sheets containing several unrelated tables.
- The user remains responsible for deciding whether the preview reflects the intended work.
- Requests and planning metadata may be written to the local `logs/plans.jsonl` file.
- 8GB RAM and 2B models are not supported targets.

## Privacy

Workbook content is processed by the bundled local model and deterministic local code. It is not sent to an external AI service.

## Licensing

ExcelAssistant source code written for this repository is released under the
[MIT License](LICENSE), copyright (c) 2026 sanhanabi.

The bundled model, llama.cpp, Python, and other dependencies are not
relicensed under the project MIT License. They retain their respective
upstream licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
