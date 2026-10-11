@echo off
cd /d "%~dp0"
if not exist ".env" (
  echo Please copy .env.example to .env and fill your settings first.
  pause
  exit /b 1
)
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency install failed.
  pause
  exit /b 1
)
py -u bot.py
if errorlevel 1 pause
