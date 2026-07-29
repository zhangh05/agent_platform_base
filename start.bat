@echo off
setlocal EnableExtensions
title Agent Platform Base

set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start.ps1" %*
set "STATUS=%ERRORLEVEL%"

if not "%STATUS%"=="0" (
  echo.
  echo [ERROR] Agent Platform Base failed to start.
  if exist "%ROOT%logs\startup-error.log" (
    echo Opening the detailed startup log...
    start "Agent Platform Base startup error" notepad.exe "%ROOT%logs\startup-error.log"
  ) else (
    echo No startup log was created. Check the error shown above.
  )
)

if not "%CI%"=="true" if not "%AGENT_PLATFORM_NO_PAUSE%"=="1" pause
exit /b %STATUS%
