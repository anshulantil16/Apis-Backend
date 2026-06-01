@echo off
title APIS India - Backend Server
cd /d %~dp0

echo ============================================
echo  APIS India Backend Server - Windows
echo ============================================

REM Activate virtual environment
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found.
    echo Run setup.bat first to create it.
    pause
    exit /b 1
)

echo [1/3] Running database migrations...
python manage.py migrate --noinput
if %errorlevel% neq 0 (
    echo [ERROR] Migrations failed. Check your .env DATABASE_URL.
    pause
    exit /b 1
)

echo [2/3] Collecting static files...
python manage.py collectstatic --noinput --clear

echo [3/3] Starting Waitress server...
python serve_windows.py

pause
