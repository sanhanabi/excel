$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv-build\Scripts\python.exe"
$cache = Join-Path $root ".nuitka-cache"
$build = Join-Path $root "build"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$release = Join-Path $root "dist\ExcelAssistant-standalone-$stamp"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Build Python was not found: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $root "models\planner.gguf"))) {
    throw "Bundled model was not found."
}
if (-not (Test-Path -LiteralPath (Join-Path $root "runtime\llama\llama-server.exe"))) {
    throw "Bundled llama.cpp runtime was not found."
}
if (-not (Test-Path -LiteralPath (Join-Path $root "licenses\Python-3.9.9.txt"))) {
    throw "Python 3.9.9 license text was not found."
}

New-Item -ItemType Directory -Force -Path $cache, $build, $release | Out-Null
$env:NUITKA_CACHE_DIR = $cache

Push-Location $root
try {
    & $python -m nuitka `
        --mode=standalone `
        --mingw64 `
        --assume-yes-for-downloads `
        --windows-console-mode=disable `
        --enable-plugin=tk-inter `
        --include-package=excel_assistant `
        --output-filename=ExcelAssistant.exe `
        --output-dir=$build `
        run.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$compiled = Get-ChildItem -LiteralPath $build -Directory -Filter "*.dist" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($null -eq $compiled) {
    throw "Nuitka output directory was not found."
}

Copy-Item -Path (Join-Path $compiled.FullName "*") -Destination $release -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "config.json") -Destination $release -Force
Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination $release -Force
Copy-Item -LiteralPath (Join-Path $root "THIRD_PARTY_NOTICES.md") -Destination $release -Force
Copy-Item -LiteralPath (Join-Path $root "licenses") -Destination $release -Recurse -Force

$licenseDir = Join-Path $release "licenses"
$sitePackages = Join-Path $root ".venv-build\Lib\site-packages"
$packageLicenseDir = Join-Path $licenseDir "python-packages"
New-Item -ItemType Directory -Force -Path $packageLicenseDir | Out-Null
if (Test-Path -LiteralPath $sitePackages) {
    Get-ChildItem -LiteralPath $sitePackages -Recurse -File |
        Where-Object {
            $_.Name -match "^(LICENSE|LICENCE|COPYING|NOTICE)(\..*)?$"
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($sitePackages.Length).TrimStart("\")
            $destination = Join-Path $packageLicenseDir $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) |
                Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
}

$modelDir = Join-Path $release "models"
$runtimeDir = Join-Path $release "runtime\llama"
New-Item -ItemType Directory -Force -Path $modelDir, $runtimeDir | Out-Null
Copy-Item -LiteralPath (Join-Path $root "models\planner.gguf") -Destination $modelDir -Force
Copy-Item -Path (Join-Path $root "runtime\llama\*") -Destination $runtimeDir -Recurse -Force

Write-Output $release
