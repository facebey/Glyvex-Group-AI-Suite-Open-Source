@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: start.cmd - Build del frontend + arranque del backend (modo produccion/todo-en-uno).
:: Uso:
::   start.cmd          # build del frontend (si hace falta) + uvicorn en :7981
::   start.cmd --dev    # informa como levantar el modo desarrollo
:: Alternativa en PowerShell: start.ps1

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%frontend"
set "DIST_DIR=%FRONTEND_DIR%\dist"

if "%GLYVEX_PORT%"=="" (set "PORT=7981") else (set "PORT=%GLYVEX_PORT%")
if "%GLYVEX_HOST%"=="" (set "HOST=127.0.0.1") else (set "HOST=%GLYVEX_HOST%")

if "%1"=="--dev" (
    echo Modo desarrollo: corre estos dos comandos en terminales separadas:
    echo   1^) cd backend  ^&^& uvicorn main:app --reload --port 7981
    echo   2^) cd frontend ^&^& npm run dev   # sirve en :5173 con proxy a /api
    exit /b 0
)

echo == Glyvex-AI-Suite: build + arranque ==

if not exist "%DIST_DIR%\" (
    echo -- No existe frontend\dist, generando build de produccion --
    cd /d "%FRONTEND_DIR%" && call npm install && call npm run build
    if errorlevel 1 (
        echo Error durante el build del frontend.
        exit /b 1
    )
) else (
    echo -- frontend\dist ya existe, se omite build ^(borra el directorio para forzar rebuild^) --
)

echo -- Arrancando backend en %HOST%:%PORT% --
cd /d "%BACKEND_DIR%"
uvicorn main:app --host %HOST% --port %PORT%
endlocal
