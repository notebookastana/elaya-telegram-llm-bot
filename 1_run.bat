@echo off
title ELAYA GPT Bot
cd /d "%~dp0"

echo ======================================================
echo   ELAYA GPT - AI-assistant based on Elaya's knowledge
echo ======================================================
echo.

REM Check virtual environment
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)

echo Activating environment...
call venv\Scripts\activate

REM Check .env
if not exist ".env" (
    echo [WARNING] .env file not found!
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [OK] Created .env from template
    ) else (
        echo # ELAYA GPT Configuration > .env
        echo TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN >> .env
        echo OLLAMA_URL=http://localhost:11434/api/generate >> .env
        echo DEFAULT_MODEL=qwen2.5:7b-instruct-q4_K_M >> .env
        echo DEEP_MODEL=mistral:7b-instruct-q4_K_M >> .env
        echo MAX_STREAM_TIMEOUT=600 >> .env
        echo [OK] Created .env file
    )
    echo [IMPORTANT] Add your bot token to .env!
    pause
    exit /b 1
)

REM Check dependencies
pip show aiogram >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
)

echo.
echo Starting ELAYA GPT...
echo ======================================================
echo.
echo RAG initializes AUTOMATICALLY on startup!
echo To stop: Ctrl+C
echo.
echo ======================================================
echo.

python bot.py

pause
