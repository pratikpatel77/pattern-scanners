@echo off
setlocal
cd /d "%~dp0"
title Pattern Scanner - SAI Research

python -c "import pandas, numpy, requests, dotenv, pyotp" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Packages are missing - running one-time setup first...
    echo.
    call setup.bat
)

if exist results.html (
    set "REFRESH="
    set /p REFRESH="  Fetch fresh prices and rescan? [y/N]: "
)
if exist results.html if /I not "%REFRESH%"=="y" goto workspace

echo ==================================================
echo   PRE-BREAKOUT PATTERN SCANNERS
echo   Stage 1-^>2  ^|  Darvas Box  ^|  Cup ^& Handle
echo ==================================================
echo.
echo   Data source  (press Enter for 1)
echo     1^) Definedge     fast
echo     2^) Kotak Neo     no login needed
echo     3^) Fyers         needs today's access token
echo     4^) Cached data   no API calls, instant
echo.
set "SRC="
set "c="
set /p c="  Choice [1-4]: "
if "%c%"=="" set SRC=--source definedge
if "%c%"=="1" set SRC=--source definedge
if "%c%"=="2" set SRC=--source kotak
if "%c%"=="3" set SRC=--source fyers
if "%c%"=="4" set SRC=--offline
if not defined SRC set SRC=--source definedge

echo.
echo   Scanner  (press Enter for all)
echo     1^) All three
echo     2^) Stage 1 -^> Stage 2
echo     3^) Darvas Box
echo     4^) Cup ^& Handle
echo.
set "SC=all"
set "s="
set /p s="  Choice [1-4]: "
if "%s%"=="2" set SC=stage
if "%s%"=="3" set SC=darvas
if "%s%"=="4" set SC=cup

echo.
echo   Timeframe  (press Enter for both)
echo     1^) Daily + Weekly
echo     2^) Daily
echo     3^) Weekly
echo.
set "TF=daily,weekly"
set "t="
set /p t="  Choice [1-3]: "
if "%t%"=="2" set TF=daily
if "%t%"=="3" set TF=weekly

echo.
python scan.py %SRC% --scanners %SC% --tf %TF%
if errorlevel 1 (
    echo Scan did not finish. Previous results will remain available.
    pause
)

:workspace
python -c "from kronos_model import Kronos" >nul 2>&1
if errorlevel 1 (
    python -m pip install -r requirements-kronos.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
echo Opening Pattern Scanner with AI predictions. Keep this window open.
python kronos_dashboard.py
if errorlevel 1 pause
