@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\dev\start-backend.ps1"
