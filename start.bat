@echo off
setlocal EnableExtensions
title LZCore

set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start.ps1" %*
set "STATUS=%ERRORLEVEL%"

if not "%STATUS%"=="0" (
  echo.
  echo [ERROR] LZCore failed to start.
  if exist "%ROOT%logs\startup-error.log" (
    echo Opening the detailed startup log...
    start "LZCore startup error" notepad.exe "%ROOT%logs\startup-error.log"
  ) else (
    echo No startup log was created. Check the error shown above.
  )
)

if not "%CI%"=="true" if not "%LZCORE_NO_PAUSE%"=="1" pause
exit /b %STATUS%
