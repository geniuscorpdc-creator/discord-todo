@echo off
cd /d "%~dp0"

echo ============================================================
echo  OSRS Todo Discord Bot
echo ============================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found.
    echo.
    echo  Install Python 3.10 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  During installation, check "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env file not found.
    echo.
    echo  Copy .env.example to .env and add your bot token and channel ID:
    echo    copy .env.example .env
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Installing dependencies...
"venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo Starting bot... Leave this window open while the bot is running.
echo Press Ctrl+C to stop the bot.
echo.

"venv\Scripts\python.exe" bot.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] The bot stopped unexpectedly.
    pause
    exit /b 1
)
