@echo off
REM Launch Ultron. Uses the project virtual environment (.venv) when it exists.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
    where py >nul 2>nul
    if not errorlevel 1 set "PYTHON=py -3"
    echo [Ultron] No .venv found - using system Python. See README.md for setup steps.
)

if not exist ".env" if exist ".env.example" (
    echo [Ultron] No .env file found - creating one from .env.example.
    copy ".env.example" ".env" >nul
)

%PYTHON% main.py %*
if errorlevel 1 (
    echo.
    echo [Ultron] Ultron exited with an error. Run "%PYTHON% main.py --check" for diagnostics,
    echo          or see logs\ultron.log.
    pause
)
endlocal
