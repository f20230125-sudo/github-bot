# Starts the Agent Desk API (127.0.0.1:8010) and the site (http://localhost:3010).
# Run from anywhere:  powershell -File <path to this folder>\dev.ps1
# Stop with Ctrl+C in each window.

$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Error "Backend virtual environment missing. Run: cd backend; py -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Error "Frontend dependencies missing. Run: cd frontend; npm install"
    exit 1
}

Start-Process powershell -WorkingDirectory $backend -ArgumentList '-NoExit', '-Command', "& '$python' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8010"
Start-Process powershell -WorkingDirectory $frontend -ArgumentList '-NoExit', '-Command', 'npm run dev'

Write-Host 'API:  http://127.0.0.1:8010/api/health'
Write-Host 'Site: http://localhost:3010'
