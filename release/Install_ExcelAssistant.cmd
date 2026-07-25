@echo off
setlocal
cd /d "%~dp0"

set "PART1=ExcelAssistant-v1.0.0-win64.zip.001"
set "PART2=ExcelAssistant-v1.0.0-win64.zip.002"
set "ARCHIVE=ExcelAssistant-v1.0.0-win64.zip"
set "EA_DEST=%CD%"
set "EXPECTED_PART1=2ae7a1f417a2dcd658eba6ddca6cff8063626f7b3c0953cc0a322c73c4e67dc0"
set "EXPECTED_PART2=05250b91114a14f57706239de64c170c7d8780cd4752a20268c045f085d7dc16"
set "EXPECTED_ARCHIVE=e8356bc870b4e20420c6387899644d50e340d9937c9534b2d32fb75e3ccd2697"

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
