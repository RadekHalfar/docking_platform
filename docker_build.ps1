#!/usr/bin/env pwsh
# Build all Docker images for MCP Docking System
#
# Usage: .\docker_build.ps1 [options]
# Options:
#   -Services <service1,service2,...>  Build specific services only
#   -NoCache                           Build without using cache
#   -Parallel                          Build images in parallel (faster)

param(
    [string[]]$Services = @(),
    [switch]$NoCache,
    [switch]$Parallel
)

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "MCP Docking System - Docker Image Builder" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# Define all services and their build configurations
$allServices = @{
    "base" = @{
        dockerfile = "docker/base.Dockerfile"
        tag = "mcp-docking-base:latest"
        description = "Base image with Python and scientific libraries"
    }
    "rcsb-downloader" = @{
        dockerfile = "docker/rcsb_downloader.Dockerfile"
        tag = "mcp-rcsb-downloader:latest"
        description = "RCSB PDB structure downloader"
        depends = @("base")
    }
    "ligand-preparer" = @{
        dockerfile = "docker/ligand_preparer.Dockerfile"
        tag = "mcp-ligand-preparer:latest"
        description = "Ligand preparation service"
        depends = @("base")
    }
    "protein-preparer" = @{
        dockerfile = "docker/protein_preparer.Dockerfile"
        tag = "mcp-protein-preparer:latest"
        description = "Protein preparation service"
        depends = @("base")
    }
    "moldina-runner" = @{
        dockerfile = "docker/moldina_runner.Dockerfile"
        tag = "mcp-moldina-runner:latest"
        description = "Moldina molecular docking service"
        depends = @("base")
    }
    "pymol-visualizer" = @{
        dockerfile = "docker/pymol_visualizer.Dockerfile"
        tag = "mcp-pymol-visualizer:latest"
        description = "PyMOL visualization service"
        depends = @("base")
    }
    "pymol-interactive" = @{
        dockerfile = "docker/pymol_interactive.Dockerfile"
        tag = "mcp-pymol-interactive:latest"
        description = "Interactive PyMOL service (noVNC + helper API)"
        depends = @("base")
    }
    "ui" = @{
        dockerfile = "Dockerfile.ui"
        tag = "mcp-docking-ui:latest"
        description = "Streamlit web UI"
        depends = @("base")
    }
}

# Determine which services to build
if ($Services.Count -eq 0) {
    $servicesToBuild = $allServices.Keys | Sort-Object
} else {
    $servicesToBuild = $Services
}

# Validate service names
foreach ($service in $servicesToBuild) {
    if (-not $allServices.ContainsKey($service)) {
        Write-Host "❌ Unknown service: $service" -ForegroundColor Red
        Write-Host "Available services: $($allServices.Keys -join ', ')" -ForegroundColor Yellow
        exit 1
    }
}

# Build base image first if any dependent services are being built
$needsBase = $false
foreach ($service in $servicesToBuild) {
    if ($allServices[$service].depends -contains "base") {
        $needsBase = $true
        break
    }
}

if ($needsBase -and -not ($servicesToBuild -contains "base")) {
    $servicesToBuild = @("base") + $servicesToBuild
}

Write-Host "Building services: $($servicesToBuild -join ', ')" -ForegroundColor Green
Write-Host ""

# Build function
function Build-DockerImage {
    param(
        [string]$ServiceName,
        [hashtable]$Config,
        [bool]$UseCache
    )
    
    Write-Host "─" * 80 -ForegroundColor DarkGray
    Write-Host "📦 Building: $ServiceName" -ForegroundColor Cyan
    Write-Host "   Description: $($Config.description)" -ForegroundColor Gray
    Write-Host "   Dockerfile: $($Config.dockerfile)" -ForegroundColor Gray
    Write-Host "   Tag: $($Config.tag)" -ForegroundColor Gray
    Write-Host ""
    
    $buildArgs = @(
        "build",
        "-f", $Config.dockerfile,
        "-t", $Config.tag
    )
    
    if (-not $UseCache) {
        $buildArgs += "--no-cache"
    }
    
    $buildArgs += "."
    
    $startTime = Get-Date
    
    try {
        & docker $buildArgs
        
        if ($LASTEXITCODE -eq 0) {
            $duration = (Get-Date) - $startTime
            Write-Host "✅ Successfully built $ServiceName in $($duration.TotalSeconds.ToString('F1'))s" -ForegroundColor Green
            Write-Host ""
            return $true
        } else {
            Write-Host "❌ Failed to build $ServiceName" -ForegroundColor Red
            Write-Host ""
            return $false
        }
    } catch {
        Write-Host "❌ Error building $ServiceName : $_" -ForegroundColor Red
        Write-Host ""
        return $false
    }
}

# Build images
$successCount = 0
$failCount = 0
$totalStartTime = Get-Date

if ($Parallel -and $servicesToBuild.Count -gt 1) {
    Write-Host "Building images in parallel..." -ForegroundColor Yellow
    Write-Host ""
    
    # Build base first (if needed)
    if ($servicesToBuild -contains "base") {
        $result = Build-DockerImage -ServiceName "base" -Config $allServices["base"] -UseCache (-not $NoCache)
        if ($result) { $successCount++ } else { $failCount++ }
        $servicesToBuild = $servicesToBuild | Where-Object { $_ -ne "base" }
    }
    
    # Build remaining services in parallel
    $jobs = @()
    foreach ($service in $servicesToBuild) {
        $config = $allServices[$service]
        $jobs += Start-Job -ScriptBlock {
            param($svc, $cfg, $cache)
            & docker build -f $cfg.dockerfile -t $cfg.tag $(if (-not $cache) { "--no-cache" }) .
        } -ArgumentList $service, $config, (-not $NoCache)
    }
    
    # Wait for all jobs
    $jobs | ForEach-Object {
        $_ | Wait-Job | Receive-Job
        if ($_.State -eq "Completed") { $successCount++ } else { $failCount++ }
    }
    
    $jobs | Remove-Job
} else {
    # Sequential build
    foreach ($service in $servicesToBuild) {
        $config = $allServices[$service]
        $result = Build-DockerImage -ServiceName $service -Config $config -UseCache (-not $NoCache)
        
        if ($result) {
            $successCount++
        } else {
            $failCount++
            Write-Host "⚠️  Stopping build due to failure" -ForegroundColor Yellow
            break
        }
    }
}

$totalDuration = (Get-Date) - $totalStartTime

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "BUILD SUMMARY" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""
Write-Host "✅ Successful: $successCount" -ForegroundColor Green
Write-Host "❌ Failed: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Gray" })
Write-Host "⏱️  Total time: $($totalDuration.TotalSeconds.ToString('F1'))s" -ForegroundColor Cyan
Write-Host ""

if ($failCount -eq 0) {
    Write-Host "🎉 All images built successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. View images: docker images | grep mcp" -ForegroundColor White
    Write-Host "  2. Start services: docker-compose up -d" -ForegroundColor White
    Write-Host "  3. Check status: docker-compose ps" -ForegroundColor White
    Write-Host "  4. View logs: docker-compose logs -f" -ForegroundColor White
} else {
    Write-Host "❌ Build failed. Check errors above." -ForegroundColor Red
    exit 1
}
