# PowerShell script to start all MCP services
# Usage:
#   .\start_services.ps1                       # auto-detect/defaults
#   .\start_services.ps1 -MoldinaPath \\wsl$\Ubuntu\home\user\Moldina\moldina
#   .\start_services.ps1 -MoldinaPath "/home/user/Moldina/moldina" -WslDistro "Ubuntu-22.04"

param(
    [string]$MoldinaPath = $env:MOLDINA_PATH,
    [string]$WslDistro   = $env:MOLDINA_WSL_DISTRO
)

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "Starting MCP Docking System Services" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# Check if virtual environment exists
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "❌ Virtual environment not found!" -ForegroundColor Red
    Write-Host "Please create it first:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\activate" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

$pythonExe = ".\.venv\Scripts\python.exe"

# Start services in separate windows
Write-Host "Starting RCSB Downloader (Port 8001)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$pythonExe' -m services.rcsb_downloader.server" -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host "Starting Ligand Preparer (Port 8002)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$pythonExe' -m services.ligand_preparer.server" -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host "Starting Protein Preparer (Port 8003)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$pythonExe' -m services.protein_preparer.server" -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host "Starting Moldina Runner (Port 8004)..." -ForegroundColor Yellow
Write-Host "  Note: Requires Moldina installed in WSL" -ForegroundColor Yellow
if ($MoldinaPath) { Write-Host "  Using MoldinaPath: $MoldinaPath" -ForegroundColor DarkGray }
if ($WslDistro)   { Write-Host "  Using WSL Distro: $WslDistro"   -ForegroundColor DarkGray }

$moldinaArgs = ""
if ($MoldinaPath) { $moldinaArgs += " --moldina-path `"$MoldinaPath`"" }
if ($WslDistro)   { $moldinaArgs += " --wsl-distro `"$WslDistro`"" }

Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$pythonExe' -m services.moldina_runner.server$moldinaArgs" -WindowStyle Normal

Start-Sleep -Seconds 3

Write-Host "Starting PyMOL Visualizer (Port 8005)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$pythonExe' -m services.pymol_visualizer.server" -WindowStyle Normal

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "All services started!" -ForegroundColor Green
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""
Write-Host "Services running at:" -ForegroundColor Cyan
Write-Host "  - RCSB Downloader:   http://localhost:8001/health" -ForegroundColor White
Write-Host "  - Ligand Preparer:   http://localhost:8002/health" -ForegroundColor White
Write-Host "  - Protein Preparer:  http://localhost:8003/health" -ForegroundColor White
Write-Host "  - Moldina Runner:    http://localhost:8004/health" -ForegroundColor White
Write-Host "  - PyMOL Visualizer:  http://localhost:8005/health" -ForegroundColor White
Write-Host ""
Write-Host "Test the workflow:" -ForegroundColor Cyan
Write-Host "  & '$pythonExe' example_prepare_ligand.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "Or with Moldina (if installed in WSL):" -ForegroundColor Cyan
Write-Host "  & '$pythonExe' example_complete_workflow.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Gray
Write-Host "=" * 80 -ForegroundColor Cyan

# Wait for user to press Ctrl+C
try {
    while ($true) {
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "Stopping services..." -ForegroundColor Yellow
}
