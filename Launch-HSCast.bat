@echo off
setlocal
cd /d "%~dp0windows"
title HSCast - Screen Casting Studio

if not exist ".venv\Scripts\python.exe" (
    echo =======================================================
    echo   Setting up HSCast Windows Application...
    echo =======================================================
    powershell -NoProfile -ExecutionPolicy Bypass -File "run.ps1" doctor
)

echo Starting HSCast Control Center...
start "" ".venv\Scripts\python.exe" -m hscast gui %*
