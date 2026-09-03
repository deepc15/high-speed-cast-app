@echo off
setlocal
cd /d "%~dp0"
title HSCast - Screen Casting Studio

if not exist ".venv\Scripts\python.exe" (
    echo [HSCast] Initializing environment and installing dependencies...
    powershell -NoProfile -ExecutionPolicy Bypass -File "run.ps1" doctor
)

echo [HSCast] Launching HSCast Windows Application...
start "" ".venv\Scripts\python.exe" -m hscast gui %*
