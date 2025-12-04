@echo off
title ELAYA GPT - Load Documents
cd /d "%~dp0"

echo ======================================================
echo   ELAYA GPT - LOAD DOCUMENTS TO KNOWLEDGE BASE
echo ======================================================
echo.

if not exist "venv\" (
    echo [ERROR] Run SETUP.bat or 1_run.bat first
    pause
    exit /b 1
)

call venv\Scripts\activate

if not exist "documents\" (
    mkdir documents
    echo [OK] Created documents\ folder
)

echo.
echo INSTRUCTIONS:
echo    1. Put documents in folder: documents\
echo    2. Supported formats: PDF, DOCX, TXT
echo    3. Elaya's lectures, books, and other materials
echo.
echo ======================================================

dir /b documents\*.pdf documents\*.docx documents\*.txt >nul 2>&1
if errorlevel 1 (
    echo [WARNING] No documents in documents\ folder!
    pause
    exit /b 0
)

echo Loading documents...
python load_documents.py

echo ======================================================
pause
