@echo off
title AI Dashboard
cd /d "C:\Users\timan\AI STACK"

REM Kill any existing streamlit on 8501
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":8501 " ^| findstr "LISTENING"') do (
    echo [*] Stopping existing dashboard (PID %%a)...
    taskkill /F /PID %%a > nul 2>&1
)

timeout /t 1 /nobreak > nul
echo [*] Starting AI Dashboard at http://localhost:8501
echo.
python -m streamlit run src\dashboard.py --server.headless=false --server.port=8501 --browser.gatherUsageStats=false
echo.
echo === DASHBOARD EXITED ===
pause
