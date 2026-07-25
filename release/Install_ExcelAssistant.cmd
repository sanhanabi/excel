@echo off
setlocal
cd /d "%~dp0"

set "PART1=ExcelAssistant-v1.0.0-win64.zip.001"
set "PART2=ExcelAssistant-v1.0.0-win64.zip.002"
set "ARCHIVE=ExcelAssistant-v1.0.0-win64.zip"
set "EA_DEST=%CD%"

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

if exist "%ARCHIVE%" del /q "%ARCHIVE%"

echo Joining the release files...
copy /b "%PART1%"+"%PART2%" "%ARCHIVE%" >nul
if errorlevel 1 (
    echo Failed to join the release files.
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
