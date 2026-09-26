@echo off
rem Launcher for the Xteink X4 / X4 Pro desktop converter (Windows).
rem Creates the virtual environment and installs dependencies on first run.
setlocal
set "DIR=%~dp0"

if not exist "%DIR%.venv\Scripts\python.exe" (
    echo First run: creating virtual environment in %DIR%.venv ...
    py -3 -m venv "%DIR%.venv" 2>nul || python -m venv "%DIR%.venv"
    if errorlevel 1 (
        echo Python 3 was not found. Install it from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    "%DIR%.venv\Scripts\python.exe" -m pip install --quiet -r "%DIR%requirements.txt"
)

start "" "%DIR%.venv\Scripts\pythonw.exe" "%DIR%gui_converter.py" %*
