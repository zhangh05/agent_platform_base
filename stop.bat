@echo off
setlocal EnableExtensions
title LZCore - Stop

set "ROOT=%~dp0"
cd /d "%ROOT%"
if /I "%~1"=="--no-pause" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%stop.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%stop.ps1" %*
)
set "STATUS=%ERRORLEVEL%"

if not "%~1"=="--no-pause" if not "%CI%"=="true" if not "%LZCORE_NO_PAUSE%"=="1" pause
exit /b %STATUS%
