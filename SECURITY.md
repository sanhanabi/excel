# Security Policy

## Supported version

Security reports are accepted for the latest published ExcelAssistant release.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature from the repository
Security tab. Do not open a public issue for a vulnerability that contains
exploitation details, sensitive workbook data, or personal information.

Include the affected version, Windows version, reproduction steps, and whether
the problem occurs in ExcelAssistant code or a bundled third-party component.
Remove real company data and credentials from all examples.

Reports concerning Qwen, llama.cpp, Python, pandas, openpyxl, or another
upstream dependency may also need to be reported to the corresponding upstream
project.

## Data handling

ExcelAssistant performs planning and workbook processing locally. Plan logging
is disabled in the distributed configuration. Diagnostic logs can contain user
requests or plan parameters if a developer explicitly enables logging; review
and redact them before sharing.

## Binary distribution

Release executables are not code-signed. Verify files against
`SHA256SUMS.txt`, and follow the security policy of the organization that owns
the target computer.
