@echo off
REM ===========================================================================
REM  AI Tutoring System - one-click setup for new teammates (Windows)
REM
REM  Prerequisites (install once):
REM    * Git for Windows  (includes Git LFS)   https://git-scm.com/download/win
REM    * Python 3.10-3.12                       https://www.python.org/downloads/
REM    * Node.js 20+                            https://nodejs.org/
REM    * Docker Desktop                         https://www.docker.com/products/docker-desktop/
REM
REM  If a tool is missing and `winget` is available, this script offers to
REM  install it for you. After any install you must close this window, open a
REM  NEW terminal, and run setup.bat again so PATH is refreshed.
REM
REM  Then: double-click this file.
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   AI Tutoring System - setup
echo ============================================================
echo.

REM --- 0. Check prerequisites, offering a winget install for any missing ----
set INSTALLED=
call :require git    "Git for Windows"  Git.Git
call :require python "Python 3.12"      Python.Python.3.12
call :require node   "Node.js (LTS)"    OpenJS.NodeJS.LTS
call :require docker "Docker Desktop"   Docker.DockerDesktop

if defined INSTALLED (
  echo.
  echo ============================================================
  echo   One or more tools were just installed.
  echo.
  echo   CLOSE this window, open a NEW terminal, and run setup.bat again.
  echo   A new terminal is required so PATH picks up the new tools.
  echo ============================================================
  echo.
  pause
  exit /b 0
)

REM --- 1. Pull large files tracked with Git LFS (the database seed) ----------
echo.
echo [1/6] Pulling Git LFS files (database_seed.sql)...
git lfs pull
if errorlevel 1 (
  echo [WARN] "git lfs pull" failed. Is Git LFS installed? Try: git lfs install
)

REM --- 2. Create .env from the template (never overwrite an existing one) ----
echo.
echo [2/6] Creating .env from .env.example...
if exist ".env" (
  echo       .env already exists - leaving it untouched.
) else (
  copy /Y ".env.example" ".env" >nul
  echo       .env created. Open it and set GROQ_API_KEY before asking questions.
)

REM --- 3. Python dependencies ------------------------------------------------
echo.
echo [3/6] Installing Python dependencies (pip)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (echo [ERROR] pip install failed. & goto :fail)

REM --- 4. Root Node dependencies (the npm run dev / db:restore harness) -------
echo.
echo [4/6] Installing root Node dependencies (concurrently)...
call npm install
if errorlevel 1 (echo [ERROR] npm install ^(root^) failed. & goto :fail)

REM --- 5. Frontend Node dependencies -----------------------------------------
echo.
echo [5/6] Installing frontend dependencies...
call npm --prefix frontend install
if errorlevel 1 (echo [ERROR] npm install ^(frontend^) failed. & goto :fail)

REM --- 6. Restore the portable database seed ---------------------------------
echo.
echo [6/6] Starting PostgreSQL and restoring the database seed...
where docker >nul 2>&1
if errorlevel 1 (
  echo       [SKIP] Docker is not available. Start your own PostgreSQL, then run:
  echo              python -m scripts.restore_db --yes
  goto :done
)
docker compose up -d
echo       Waiting for PostgreSQL to become healthy...
set READY=
for /l %%i in (1,1,30) do (
  if not defined READY (
    for /f "delims=" %%s in ('docker inspect -f "{{.State.Health.Status}}" ai_tutoring_pg 2^>nul') do (
      if "%%s"=="healthy" set READY=1
    )
    if not defined READY timeout /t 2 /nobreak >nul
  )
)
if defined READY (
  echo       PostgreSQL is healthy. Restoring seed...
  call npm run db:restore
  if errorlevel 1 (echo [WARN] Database restore failed. Run "npm run db:restore" manually. & goto :done)
) else (
  echo       [WARN] PostgreSQL did not report healthy in time.
  echo              Run "npm run db:restore" once it is up.
)

:done
echo.
echo ============================================================
echo   Setup complete.
echo.
echo   Start the app with:
echo       npm run dev
echo.
echo   Then open:  http://localhost:5173
echo   (the backend API runs on http://127.0.0.1:5000 - do not open it directly)
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   Setup FAILED. Fix the error above and run setup.bat again.
echo ============================================================
echo.
pause
exit /b 1


REM ---------------------------------------------------------------------------
REM  :require <command> <"Display Name"> <winget-id>
REM  Verifies a command is on PATH. If not, offers a winget install. Sets
REM  INSTALLED=1 when something was installed, so the caller can tell the user
REM  to relaunch for a fresh PATH.
REM ---------------------------------------------------------------------------
:require
where %~1 >nul 2>&1
if not errorlevel 1 goto :eof

echo [MISSING] %~2 was not found on PATH.
where winget >nul 2>&1
if errorlevel 1 (
  echo           winget is not available. Install %~2 manually ^(see the URL in this file's header^),
  echo           then run setup.bat again.
  goto :eof
)

choice /C YN /N /M "           Install %~2 now with winget? [Y/N] "
if errorlevel 2 (
  echo           Skipped. Install %~2 manually, then run setup.bat again.
  goto :eof
)

echo           Installing %~2 via winget...
winget install --id %~3 -e --silent --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
  echo           [WARN] winget install for %~2 failed. Install it manually.
) else (
  echo           Installed %~2.
  set INSTALLED=1
)
goto :eof
