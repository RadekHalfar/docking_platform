#!/usr/bin/env pwsh
# Restart all MCP servers with venv Python

Write-Host "🔄 Restarting all MCP servers with venv Python..." -ForegroundColor Cyan
Write-Host ""

# Cleanup old output
Write-Host "🧹 Cleaning old output..." -ForegroundColor Yellow
Remove-Item -Recurse -Force ./demo_output/prepared_proteins, ./demo_output/prepared_ligands, ./demo_output/docking_results -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Starting servers in separate windows..." -ForegroundColor Green
Write-Host "  - RCSB Downloader on port 8001"
Write-Host "  - Ligand Preparer on port 8002"  
Write-Host "  - Protein Preparer on port 8003"
Write-Host "  - Moldina Runner on port 8004"
Write-Host "  - PyMOL Visualizer on port 8005"
Write-Host ""

# Start RCSB Downloader
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; Write-Host 'RCSB Downloader Server' -ForegroundColor Green; .venv\Scripts\python.exe -m services.rcsb_downloader.server"

# Start Ligand Preparer  
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; Write-Host 'Ligand Preparer Server' -ForegroundColor Green; .venv\Scripts\python.exe -m services.ligand_preparer.server"

# Start Protein Preparer
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; Write-Host 'Protein Preparer Server' -ForegroundColor Green; .venv\Scripts\python.exe -m services.protein_preparer.server"

# Start Moldina Runner
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; Write-Host 'Moldina Runner Server' -ForegroundColor Green; .venv\Scripts\python.exe -m services.moldina_runner.server --moldina-path '/home/radek/Moldina/moldina'"

# Start PyMOL Visualizer
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; Write-Host 'PyMOL Visualizer Server' -ForegroundColor Green; .venv\Scripts\python.exe -m services.pymol_visualizer.server"

Write-Host "✅ All servers started!" -ForegroundColor Green
Write-Host ""
Write-Host "Waiting 5 seconds for servers to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "🏥 Health checks:" -ForegroundColor Cyan
try { $r = Invoke-WebRequest -Uri "http://localhost:8001/health" -UseBasicParsing; Write-Host "  ✅ RCSB Downloader: $($r.StatusCode)" -ForegroundColor Green } catch { Write-Host "  ❌ RCSB Downloader failed" -ForegroundColor Red }
try { $r = Invoke-WebRequest -Uri "http://localhost:8002/health" -UseBasicParsing; Write-Host "  ✅ Ligand Preparer: $($r.StatusCode)" -ForegroundColor Green } catch { Write-Host "  ❌ Ligand Preparer failed" -ForegroundColor Red }
try { $r = Invoke-WebRequest -Uri "http://localhost:8003/health" -UseBasicParsing; Write-Host "  ✅ Protein Preparer: $($r.StatusCode)" -ForegroundColor Green } catch { Write-Host "  ❌ Protein Preparer failed" -ForegroundColor Red }
try { $r = Invoke-WebRequest -Uri "http://localhost:8004/health" -UseBasicParsing; Write-Host "  ✅ Moldina Runner: $($r.StatusCode)" -ForegroundColor Green } catch { Write-Host "  ❌ Moldina Runner failed" -ForegroundColor Red }
try { $r = Invoke-WebRequest -Uri "http://localhost:8005/health" -UseBasicParsing; Write-Host "  ✅ PyMOL Visualizer: $($r.StatusCode)" -ForegroundColor Green } catch { Write-Host "  ❌ PyMOL Visualizer failed" -ForegroundColor Red }

Write-Host ""
Write-Host "🚀 Ready to run workflows!" -ForegroundColor Green
Write-Host "   Run: python example_complete_workflow.py" -ForegroundColor Cyan
