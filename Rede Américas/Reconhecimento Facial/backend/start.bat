@echo off
echo Matando processos Python na porta 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr LISTENING') do (
    echo Matando PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 >nul

echo Subindo backend na porta 8000...
cd /d "%~dp0"
python -m uvicorn main:app --port 8000 --reload
