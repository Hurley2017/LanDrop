@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Check if Python is on PATH, otherwise check default user installation path
set "PYTHON_EXE=python.exe"
where python.exe >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    )
)

echo Starting LanDrop...
"%PYTHON_EXE%" main.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [LanDrop] Application exited with code %ERRORLEVEL%.
    pause
)
