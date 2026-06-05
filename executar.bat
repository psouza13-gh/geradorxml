@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo Erro ao iniciar. Execute instalar.bat primeiro.
    pause
)
