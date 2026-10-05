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
REM  Then: double-click this file.
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   AI Tutoring System - setup
echo ============================================================
echo.

REM --- 0. Sanity-check the tools we are about to call -----------------------
where git >nul 2>&1 || (echo [ERROR] git is not on PATH. Install Git for Windows first. & goto :fail)
where python >nul 2>&1 || (echo [ERROR] python is not on PATH. Install Python 3.10-3.12 first. & goto :fail)
where npm >nul 2>&1 || (echo [ERROR] npm is not on PATH. Install Node.js first. & goto :fail)
where docker >nul 2>&1 || (echo [WARN] docker is not on PATH. Database restore will be skipped. & set NO_DOCKER=1)

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
if defined NO_DOCKER (
  echo       [SKIP] Docker not found. Start your own PostgreSQL and run:
  echo              python -m scripts.restore_db --yes
) else (
  docker compose up -d
  echo       Waiting for PostgreSQL to become healthy...
  REM Poll the container health; give it up to ~60s.
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
    goto :done
  )
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
