# Start the whole ResearchFlow stack (databases, backend, worker, frontend) and open the app.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1          # start
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Build   # rebuild images first (after code changes to the frontend or dependencies)
#
# Stop everything with:  docker compose stop

param([switch]$Build)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running. Start Docker Desktop, wait until it says 'Engine running', then run this again." -ForegroundColor Red
    exit 1
}

if ($Build) {
    Write-Host "Rebuilding images..." -ForegroundColor Cyan
    docker compose build backend frontend
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Starting containers..." -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

function Wait-Healthy($container, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $state = docker inspect -f "{{.State.Health.Status}}" $container 2>$null
        if ($state -eq "healthy") { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

Write-Host "Waiting for the backend..." -ForegroundColor Cyan
if (-not (Wait-Healthy "researchflow-backend" 240)) {
    Write-Host "Backend did not become healthy. Check: docker compose logs backend" -ForegroundColor Red
    exit 1
}

Write-Host "Applying database migrations..." -ForegroundColor Cyan
docker compose exec -T backend alembic upgrade head
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Waiting for the frontend..." -ForegroundColor Cyan
if (-not (Wait-Healthy "researchflow-frontend" 120)) {
    Write-Host "Frontend did not become healthy. Check: docker compose logs frontend" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "ResearchFlow is running:" -ForegroundColor Green
Write-Host "  App (frontend)    http://localhost:3000"
Write-Host "  Backend API docs  http://localhost:8000/docs"
Write-Host "  MinIO console     http://localhost:9001"
Write-Host "  Neo4j browser     http://localhost:7474"
Write-Host ""
Write-Host "Stop everything with: docker compose stop"

Start-Process "http://localhost:3000/login"
