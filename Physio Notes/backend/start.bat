@echo off

:: ================================================
:: CONFIGURACAO — altere a porta aqui se necessario
set PORT=8000
:: ================================================

cd /d "%~dp0"
echo Instalando dependencias...
pip install -r requirements.txt
echo.
echo Subindo Physio Notes backend...
echo Acesse: http://localhost:%PORT%
echo.
uvicorn main:app --host 0.0.0.0 --port %PORT%
pause
