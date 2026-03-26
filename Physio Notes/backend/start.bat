@echo off
cd /d "%~dp0"
echo Instalando dependencias...
pip install -r requirements.txt
echo.
echo Subindo Physio Notes backend...
echo Acesse: http://localhost:8000
echo.
uvicorn main:app --reload
pause
