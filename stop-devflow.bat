@echo off
chcp 65001 >nul
title DevFlow Stopper
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-devflow.ps1"
pause
