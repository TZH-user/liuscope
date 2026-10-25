@echo off
rem One-click launcher for the Qt frontend (v0.5.0-qt). Starts without a console window.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw qt\main.py
) else (
  start "" python qt\main.py
)
