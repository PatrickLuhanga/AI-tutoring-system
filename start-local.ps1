# ============================================================================
# start-local.ps1 - bring the whole stack up on Windows
#
# Usage (from the project root):
#   .\start-local.ps1
#
# Starts, in order: Postgres (Docker) -> Ollama check -> Flask API -> Vite UI
# Each long-running service is launched in its own window so you can read its
# logs and Ctrl+C it independently.
# ============================================================================

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

# --- 0. Preflight ------------------------------------------------------------
Step "Preflight"

if (-not (Test-Path $venvPy)) {
    throw "Virtualenv not found at $venvPy - run: python -m venv .venv; then pip install -r requirements.txt"
}
Ok "venv present"

foreach ($f in @('.env', 'frontend\.env')) {
    if (-not (Test-Path (Join-Path $root $f))) { throw "Missing $f - copy it from $f.example" }
}
Ok "env files present"

# --- 1. Postgres + pgvector --------------------------------------------------
Step "Postgres (Docker, port 5433)"

$dockerUp = $false
try { docker info --format '{{.ServerVersion}}' *> $null; $dockerUp = $true } catch {}
if (-not $dockerUp) {
    Warn "Docker Desktop is not running. Start it, then re-run this script."
    Warn "Everything else below will fail without the database."
    exit 1
}

docker compose up -d | Out-Null

# Wait for the container healthcheck to go green (max 60s).
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    $h = docker inspect --format '{{.State.Health.Status}}' ai_tutoring_pg 2>$null
    if ($h -eq 'healthy') { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if ($ready) { Ok "ai_tutoring_pg healthy on 5433" } else { Warn "container not healthy yet - check: docker compose ps" }

# --- 2. Ollama (local LLM) ---------------------------------------------------
Step "Ollama (local LLM, port 11434)"

if (-not (Test-Path $ollamaExe)) {
    Warn "Ollama not installed. Install it, then: ollama pull qwen3:4b"
} else {
    $listening = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
        Warn "Ollama is installed but not running. Launch the Ollama app (or run 'ollama serve')."
    } else {
        Ok "listening on 11434"
        $haveModel = (& $ollamaExe list 2>$null | Select-String 'qwen3')
        if ($haveModel) { Ok "qwen3 model present" }
        else { Warn "no qwen3 model - run: ollama pull qwen3:4b   (chat will 502 until then)" }
    }
}

# --- 3. Flask API gateway ----------------------------------------------------
Step "Flask API gateway (port 5000)"

if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
    Warn "port 5000 already in use - a gateway may already be running"
} else {
    Start-Process -FilePath $venvPy -ArgumentList 'app.py' -WorkingDirectory $root
    Start-Sleep -Seconds 6
    if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
        Ok "gateway up on http://127.0.0.1:5000"
    } else {
        Warn "gateway did not come up - check the window for errors"
    }
}

# --- 4. Vite dev server ------------------------------------------------------
Step "Vite dev server (port 5173)"

if (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) {
    Warn "port 5173 already in use - stopping whatever is holding it"
    Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev' -WorkingDirectory (Join-Path $root 'frontend')
Start-Sleep -Seconds 8

if (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) {
    Ok "UI up on http://localhost:5173"
} else {
    Warn "Vite did not come up - check the window for errors"
}

# --- 5. Summary --------------------------------------------------------------
Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  UI      http://localhost:5173" -ForegroundColor Green
Write-Host "  API     http://127.0.0.1:5000/api/health" -ForegroundColor Green
Write-Host "  DB      localhost:5433 (ai_tutoring / tutor_admin)" -ForegroundColor Green
Write-Host "`n  First load after a Vite start can hang on 'Loading view...'" -ForegroundColor Yellow
Write-Host "  - just reload the page once. Harmless dev-server quirk." -ForegroundColor Yellow
Write-Host "========================================================`n" -ForegroundColor Cyan
