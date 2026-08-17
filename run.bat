@echo off
rem Cycling Progress Tracker - launcher
rem Starts the local engine and opens the UI in your browser.
rem Uses the project's .venv when present, otherwise the system Python.
setlocal
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found on PATH. Install Python 3.9+ then run again.
    pause
    exit /b 1
)

%PY% main.py
if %errorlevel% neq 0 pause
