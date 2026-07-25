@echo off
setlocal
cd /d "%~dp0"

set "PART1=ExcelAssistant-v1.0.0-win64.zip.001"
set "PART2=ExcelAssistant-v1.0.0-win64.zip.002"
set "ARCHIVE=ExcelAssistant-v1.0.0-win64.zip"
set "EA_DEST=%CD%"
set "EXPECTED_PART1=6ea75f1c1ff02523adae36cb4d0535edca07f21a88e4b3803fcbc8324581cb53"
set "EXPECTED_PART2=33442f605f8f57f645bf009dffbf7f532cc3f55d0028b07354ec90bada39f1c9"
set "EXPECTED_ARCHIVE=388c080dbe70e82e5abf33cf849d5c26e4c73379771aa607d56b7f811e8fe69f"

if not exist "%PART1%" (
    echo Missing file: %PART1%
    pause
    exit /b 1
)

if not exist "%PART2%" (
    echo Missing file: %PART2%
    pause
    exit /b 1
)

echo Verifying downloaded files...
set "ACTUAL_PART1="
for /f "delims=" %%H in ('powershell.exe -NoProfile -Command "(Get-FileHash -LiteralPath $env:PART1 -Algorithm SHA256).Hash.ToLowerInvariant()"') do set "ACTUAL_PART1=%%H"
if /i not "%ACTUAL_PART1%"=="%EXPECTED_PART1%" (
    echo Verification failed: %PART1%
    echo Download the file again before continuing.
    pause
    exit /b 1
)

set "ACTUAL_PART2="
for /f "delims=" %%H in ('powershell.exe -NoProfile -Command "(Get-FileHash -LiteralPath $env:PART2 -Algorithm SHA256).Hash.ToLowerInvariant()"') do set "ACTUAL_PART2=%%H"
if /i not "%ACTUAL_PART2%"=="%EXPECTED_PART2%" (
    echo Verification failed: %PART2%
    echo Download the file again before continuing.
    pause
    exit /b 1
)

if exist "%ARCHIVE%" del /q "%ARCHIVE%"

echo Joining the release files...
copy /b "%PART1%"+"%PART2%" "%ARCHIVE%" >nul
if errorlevel 1 (
    echo Failed to join the release files.
    pause
    exit /b 1
)

set "ACTUAL_ARCHIVE="
for /f "delims=" %%H in ('powershell.exe -NoProfile -Command "(Get-FileHash -LiteralPath $env:ARCHIVE -Algorithm SHA256).Hash.ToLowerInvariant()"') do set "ACTUAL_ARCHIVE=%%H"
if /i not "%ACTUAL_ARCHIVE%"=="%EXPECTED_ARCHIVE%" (
    echo Verification failed after joining the archive.
    del /q "%ARCHIVE%"
    pause
    exit /b 1
)

echo Extracting ExcelAssistant...
tar.exe -xf "%ARCHIVE%" -C "%EA_DEST%"
if errorlevel 1 (
    echo Failed to extract the release archive.
    pause
    exit /b 1
)

del /q "%ARCHIVE%"

echo.
echo ExcelAssistant is ready.
echo Open the ExcelAssistant-v1.0.0 folder and run ExcelAssistant.exe.
start "" "%CD%\ExcelAssistant-v1.0.0"
pause
