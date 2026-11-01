@echo off
rem LiuScope one-click launcher.
rem Prefers the frozen build (dist\LiuScope.exe); falls back to running from
rem source (qt\main.py, console-free via pythonw) when dist has no exe yet.
cd /d "%~dp0"
if exist "dist\LiuScope.exe" (
  start "" "%~dp0dist\LiuScope.exe"
) else (
  where pythonw >nul 2>nul
  if %errorlevel%==0 (
    start "" pythonw qt\main.py
  ) else (
    start "" python qt\main.py
  )
)
