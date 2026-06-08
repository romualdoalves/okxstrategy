@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo  Trading Platform — Inicializando...
echo ============================================================
echo.

REM ── Verifica Python ──────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale em https://www.python.org/downloads/ e marque "Add to PATH".
    pause
    exit /b 1
)

REM ── Verifica Node / npm ──────────────────────────────────────
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERRO] npm nao encontrado no PATH.
    echo Instale Node.js em https://nodejs.org/
    pause
    exit /b 1
)

REM ── Instala dependencias Python (silencioso se ja instalado) ──
echo [1/3] Verificando dependencias Python...
python -m pip install -q -r "%ROOT%\requirements.txt"
if errorlevel 1 (
    echo [AVISO] Falha ao instalar alguma dependencia Python. Verifique requirements.txt.
)

REM ── Instala dependencias Node se node_modules ausente ─────────
if not exist "%ROOT%\frontend\node_modules" (
    echo [2/3] Instalando dependencias do frontend...
    pushd "%ROOT%\frontend"
    npm install --silent
    popd
) else (
    echo [2/3] Dependencias do frontend ja instaladas.
)

echo [3/3] Iniciando servicos...
echo.

REM ── Inicia Backend em nova janela ─────────────────────────────
start "Trading Backend (API)" cmd /k "cd /d "%ROOT%" && python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000"

REM Aguarda o backend subir antes de abrir o frontend
timeout /t 3 /nobreak >nul

REM ── Inicia Frontend em nova janela ───────────────────────────
start "Trading Frontend (Vite)" cmd /k "cd /d "%ROOT%\frontend" && npm run dev"

REM Aguarda Vite subir e abre o navegador
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"

echo ============================================================
echo  Backend  : http://localhost:8000
echo  Frontend : http://localhost:5173
echo  API Docs : http://localhost:8000/docs
echo ============================================================
echo.
echo Duas janelas de terminal foram abertas (backend e frontend).
echo Feche-as para encerrar a aplicacao.
echo.
pause
endlocal
