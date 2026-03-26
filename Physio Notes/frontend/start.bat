@echo off
cd /d "%~dp0"
echo Subindo frontend Physio Notes...
echo Acesse: http://localhost:3000
echo.
python -m http.server 3000
pause
