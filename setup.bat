@echo off
setlocal
cd /d "%~dp0"
title Pattern Scanners - setup

echo ============================================
echo   PATTERN SCANNERS - ONE-TIME SETUP
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo   Python was not found.
    echo   Install Python 3.10 or newer from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" in the installer, then run this again.
    echo.
    pause
    exit /b 1
)

echo   Installing packages...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   Package install failed - see the message above.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo   Created .env - opening it so you can paste your broker details.
    echo   Fill in only the broker you use, save, and close Notepad.
    notepad ".env"
) else (
    echo.
    echo   .env already exists - left as is.
)

echo.
echo   Setup complete. Double-click "Run Pattern Scanners.bat" to scan.
echo   (No broker? Choose option 4 - cached data - or use --source local.)
echo.
pause
