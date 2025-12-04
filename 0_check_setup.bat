@echo off
title ELAYA GPT - Check Setup
cd /d "%~dp0"

echo ======================================================
echo   ELAYA GPT - ENVIRONMENT CHECK
echo ======================================================
echo.

REM Check Python
echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo [TIP] Install Python 3.8+ from python.org
    goto :error
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VER=%%i
echo [OK] Python %PYTHON_VER%
echo.

REM Check virtual environment
echo [2/6] Checking virtual environment...
if exist "venv\" (
    echo [OK] Virtual environment found
) else (
    echo [WARNING] Virtual environment not found
    echo [TIP] It will be created on first run
)
echo.

REM Check .env
echo [3/6] Checking configuration file...
if exist ".env" (
    echo [OK] .env file found
    findstr /C:"TELEGRAM_BOT_TOKEN=YOUR" .env >nul
    if not errorlevel 1 (
        echo [WARNING] TELEGRAM_BOT_TOKEN not configured!
        echo [TIP] Edit .env and add token from @BotFather
    )
) else (
    echo [WARNING] .env file not found
    echo [TIP] It will be created on first run
)
echo.

REM Check Ollama
echo [4/6] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Ollama is not running!
    echo [TIP] Run: ollama serve
    echo [TIP] Or install from ollama.ai
    set OLLAMA_OK=0
) else (
    echo [OK] Ollama is running
    set OLLAMA_OK=1
)
echo.

REM Check Ollama models
if %OLLAMA_OK%==1 (
    echo [5/6] Checking Ollama models...
    curl -s http://localhost:11434/api/tags > temp_models.json
    findstr /C:"qwen2.5" temp_models.json >nul
    if errorlevel 1 (
        echo [WARNING] Model qwen2.5 not found
        echo [TIP] Install: ollama pull qwen2.5:7b-instruct-q4_K_M
    ) else (
        echo [OK] qwen2.5 installed
    )
    
    findstr /C:"mistral" temp_models.json >nul
    if errorlevel 1 (
        echo [WARNING] Model mistral not found
        echo [TIP] Install: ollama pull mistral:7b-instruct-q4_K_M
    ) else (
        echo [OK] mistral installed
    )
    del temp_models.json >nul 2>&1
) else (
    echo [5/6] Skipped (Ollama not running)
)
echo.

REM Check Python packages
echo [6/6] Checking Python packages...
if exist "venv\" (
    call venv\Scripts\activate
    
    python -c "import aiogram" >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] aiogram not installed
    ) else (
        echo [OK] aiogram
    )
    
    python -c "import langchain" >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] langchain not installed
    ) else (
        echo [OK] langchain
    )
    
    python -c "import chromadb" >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] chromadb not installed
    ) else (
        echo [OK] chromadb
    )
) else (
    echo [WARNING] Virtual environment not created
    echo [TIP] Run 1_run.bat to install
)
echo.

echo ======================================================
echo   CHECK RESULTS
echo ======================================================
echo.
echo [OK] - All good
echo [WARNING] - Needs attention
echo [ERROR] - Critical error
echo.
echo NEXT STEPS:
echo    1. Install Ollama (if not yet)
echo    2. Run: ollama serve
echo    3. Download models with commands above
echo    4. Configure .env file (bot token)
echo    5. Load documents: 2_load_documents.bat
echo    6. Start bot: 1_run.bat
echo.
echo RAG initializes AUTOMATICALLY on startup!
echo.
echo ======================================================
pause
goto :eof

:error
echo.
echo [ERROR] Critical error!
echo.
pause
exit /b 1
