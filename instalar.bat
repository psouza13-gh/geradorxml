@echo off
echo ============================================
echo  NFS-e Downloader - Instalacao
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado. Instale em python.org
    pause
    exit /b 1
)

echo Instalando dependencias...
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERRO ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Instalacao concluida!
echo  Execute: python main.py
echo ============================================
pause
