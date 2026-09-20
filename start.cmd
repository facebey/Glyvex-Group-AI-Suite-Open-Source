@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: start.cmd - Build del frontend + arranque del backend (modo produccion/todo-en-uno).
:: Uso:
::   start.cmd                       # instancia de produccion
::   start.cmd --env testing         # instancia de testing (otros puertos y data)
::   start.cmd --dev                 # informa como levantar el modo desarrollo
::   start.cmd --env testing --dev   # idem, con los puertos de testing
::
:: El entorno se define en environments\<nombre>.env: ese archivo es la UNICA
:: fuente de puertos y rutas de la instancia (lo lee tambien vite.config.js).
:: Alternativa en PowerShell: start.ps1

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%frontend"
set "DIST_DIR=%FRONTEND_DIR%\dist"

set "ENV_NAME=production"
set "DEV_MODE=0"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--env" (
    if "%~2"=="" (
        echo Error: --env necesita un valor ^(production^|testing^)
        exit /b 2
    )
    set "ENV_NAME=%~2"
    shift & shift
    goto parse_args
)
if /i "%~1"=="--dev" (
    set "DEV_MODE=1"
    shift
    goto parse_args
)
echo Error: opcion desconocida '%~1' ^(usa --env ^<nombre^> o --dev^)
exit /b 2
:args_done

set "ENV_FILE=%ROOT_DIR%environments\%ENV_NAME%.env"
if not exist "%ENV_FILE%" (
    echo Error: no existe %ENV_FILE%
    echo Entornos disponibles:
    for %%F in ("%ROOT_DIR%environments\*.env") do echo   %%~nF
    exit /b 1
)

:: Carga del .env: KEY=value, ignorando vacias y comentarios.
:: "eol=#" descarta las lineas de comentario y "tokens=1,* delims==" parte en
:: el PRIMER "=" nada mas, asi un valor que contenga "=" (por ejemplo una URL
:: con query string) no se trunca.
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    set "KEY=%%A"
    set "VALUE=%%B"
    :: Recorte de espacios alrededor del "=" (delims no lo hace por si solo).
    for /f "tokens=* delims= " %%K in ("!KEY!") do set "KEY=%%K"
    for /f "tokens=* delims= " %%V in ("!VALUE!") do set "VALUE=%%V"
    if not "!KEY!"=="" set "!KEY!=!VALUE!"
)

if "%GLYVEX_PORT%"=="" (set "PORT=7981") else (set "PORT=%GLYVEX_PORT%")
if "%GLYVEX_HOST%"=="" (set "HOST=127.0.0.1") else (set "HOST=%GLYVEX_HOST%")
if "%GLYVEX_DEV_PORT%"=="" (set "DEV_PORT=5173") else (set "DEV_PORT=%GLYVEX_DEV_PORT%")
if "%GLYVEX_DATA_DIR%"=="" (set "DATA_DIR=.\data") else (set "DATA_DIR=%GLYVEX_DATA_DIR%")

if "%DEV_MODE%"=="1" (
    echo Modo desarrollo ^(entorno: %ENV_NAME%^): corre estos dos comandos en terminales separadas:
    echo.
    echo   1^) Backend ^(queda escuchando en %HOST%:%PORT%^):
    echo        cd backend
    echo        set "GLYVEX_DATA_DIR=%DATA_DIR%"
    echo        uvicorn main:app --reload --host %HOST% --port %PORT%
    echo.
    echo   2^) Frontend ^(abris http://localhost:%DEV_PORT%^):
    echo        cd frontend
    if /i "%ENV_NAME%"=="production" (
        echo        npm run dev
    ) else (
        echo        npm run dev -- --mode %ENV_NAME%
    )
    echo.
    exit /b 0
)

echo == Glyvex-AI-Suite: build + arranque ^(entorno: %ENV_NAME%^) ==

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

echo -- Datos en %DATA_DIR% --
echo -- Arrancando backend en %HOST%:%PORT% --
cd /d "%BACKEND_DIR%"
uvicorn main:app --host %HOST% --port %PORT%
endlocal
