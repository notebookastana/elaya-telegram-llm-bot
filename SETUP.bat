@echo off
title ELAYA GPT - Setup
cd /d "%~dp0"

echo ============================================================
echo   ELAYA GPT - FULL INSTALLATION
echo   AI-assistant based on Elaya's knowledge
echo ============================================================
echo.

REM ============================================================
REM STEP 1: CHECK PYTHON
REM ============================================================
echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo [TIP] Install Python 3.8+ from python.org
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VER=%%i
echo [OK] Python %PYTHON_VER% found
echo.

REM ============================================================
REM STEP 2: CREATE VIRTUAL ENVIRONMENT
REM ============================================================
echo [2/7] Creating virtual environment...
if exist "venv\" (
    echo [SKIP] venv already exists
) else (
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)
echo.

REM ============================================================
REM STEP 3: ACTIVATE ENVIRONMENT
REM ============================================================
echo [3/7] Activating virtual environment...
call venv\Scripts\activate
if errorlevel 1 (
    echo [ERROR] Activation failed
    pause
    exit /b 1
)
echo [OK] Environment activated
echo.

REM ============================================================
REM STEP 4: INSTALL DEPENDENCIES
REM ============================================================
echo [4/7] Installing dependencies...
echo [WAIT] This will take 5-15 minutes (downloading ~2GB)
echo.

REM Update pip
echo Updating pip...
python -m pip install --upgrade pip --quiet

REM Install main dependencies
echo Installing main packages...
pip install aiogram aiosqlite python-dotenv requests --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install main packages
    pause
    exit /b 1
)
echo [OK] Main packages installed

REM Install RAG dependencies
echo Installing RAG system...
pip install langchain langchain-community langchain-text-splitters langchain-chroma sentence-transformers pypdf docx2txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install RAG
    pause
    exit /b 1
)
echo [OK] RAG system installed
echo.

REM ============================================================
REM STEP 5: CREATE .ENV
REM ============================================================
echo [5/7] Creating configuration...
if not exist ".env" (
    echo # ELAYA GPT Configuration > .env
    echo TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN >> .env
    echo OLLAMA_URL=http://localhost:11434/api/generate >> .env
    echo DEFAULT_MODEL=qwen2.5:7b-instruct-q4_K_M >> .env
    echo DEEP_MODEL=mistral:7b-instruct-q4_K_M >> .env
    echo MAX_STREAM_TIMEOUT=600 >> .env
    echo.
    echo [OK] Created .env file
    echo [IMPORTANT] Edit .env and add your bot token!
) else (
    echo [OK] .env file already exists
)
echo.

REM ============================================================
REM STEP 6: CREATE FOLDERS
REM ============================================================
echo [6/7] Creating folders...
if not exist "documents\" (
    mkdir documents
    echo [OK] Created documents\
) else (
    echo [OK] documents\ exists
)

if not exist "data\" (
    mkdir data
    echo [OK] Created data\
) else (
    echo [OK] data\ exists
)
echo.

REM ============================================================
REM STEP 7: CHECK OLLAMA
REM ============================================================
echo [7/7] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Ollama is not running or not installed
    echo.
    echo WHAT TO DO:
    echo    1. Install Ollama from https://ollama.ai
    echo    2. Open new CMD and run: ollama serve
    echo    3. Download models:
    echo       ollama pull qwen2.5:7b-instruct-q4_K_M
    echo       ollama pull mistral:7b-instruct-q4_K_M
    echo.
) else (
    echo [OK] Ollama is available
    
    REM Check models
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
)
echo.

REM ============================================================
REM INSTALLATION COMPLETE
REM ============================================================
echo ============================================================
echo   INSTALLATION COMPLETE!
echo ============================================================
echo.
echo NEXT STEPS:
echo.
echo 1. Edit .env file:
echo    - Add your token from @BotFather
echo.
echo 2. Load documents into knowledge base:
echo    - Put documents in documents\ folder
echo    - Run: 2_load_documents.bat
echo.
echo 3. Start the bot:
echo    - Run: 1_run.bat
echo    - RAG initializes AUTOMATICALLY!
echo    - In Telegram: /start
echo.
echo Project files:
echo    - bot.py - main code
echo    - config.py - settings
echo    - rag_manager.py - RAG system
echo    - .env - tokens (SECRET!)
echo.
echo Ollama must be running in background!
echo Open new CMD and run: ollama serve
echo.
echo ============================================================
pause
