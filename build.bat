@echo off
REM ============================================================
REM  Build Insurance Automation into a standalone .exe
REM ============================================================
echo.
echo ========================================
echo   Building Insurance Automation .exe
echo ========================================
echo.

REM Activate venv if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Install PyInstaller if missing
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Run PyInstaller
echo.
echo Running PyInstaller...
pyinstaller --clean --noconfirm insurance_automation.spec

if errorlevel 1 (
    echo.
    echo BUILD FAILED! Check errors above.
    pause
    exit /b 1
)

REM Copy runtime files that should live alongside the exe
echo.
echo Copying runtime files...

set DIST=dist\InsuranceAutomation

REM .env (user must fill in their API key)
if exist ".env" (
    copy /Y ".env" "%DIST%\.env" >nul
    echo   Copied .env
) else (
    echo OPENROUTER_API_KEY=your-openrouter-key-here> "%DIST%\.env"
    echo AI_MODEL=openai/gpt-5.4-pro>> "%DIST%\.env"
    echo # To switch to Gemini 3 Pro, change AI_MODEL=google/gemini-3-pro>> "%DIST%\.env"
    echo   Created placeholder .env
)

REM Create runtime directories
if not exist "%DIST%\cases" mkdir "%DIST%\cases"
if not exist "%DIST%\data" mkdir "%DIST%\data"
if not exist "%DIST%\watch" mkdir "%DIST%\watch"
if not exist "%DIST%\output" mkdir "%DIST%\output"

echo.
echo ========================================
echo   BUILD COMPLETE!
echo ========================================
echo.
echo Output: %DIST%\InsuranceAutomation.exe
echo.
echo To distribute:
echo   1. Copy the entire "%DIST%" folder
echo   2. Edit .env with your OPENROUTER_API_KEY (and optionally set AI_MODEL)
echo   3. Double-click InsuranceAutomation.exe
echo.
pause
