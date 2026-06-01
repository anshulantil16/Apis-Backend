@echo off
title APIS India - First Time Setup
cd /d %~dp0

echo ============================================
echo  APIS India Backend - First Time Setup
echo ============================================

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Download from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/4] Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo [3/4] Checking .env file...
if not exist .env (
    copy .env.example .env
    echo [!] .env file created from .env.example
    echo [!] PLEASE EDIT .env with your actual values before running start.bat
    notepad .env
) else (
    echo [OK] .env already exists.
)

echo [4/4] Running initial migrations...
python manage.py migrate --noinput

echo.
echo ============================================
echo  Setup complete! Run start.bat to launch.
echo ============================================
pause
