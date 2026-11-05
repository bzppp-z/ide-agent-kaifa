@echo off
chcp 65001 >nul
title DevFlow Launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-devflow.ps1" %*
if errorlevel 1 (
  echo.
  echo [DevFlow] Failed to start. Check the messages above.
  pause
)
