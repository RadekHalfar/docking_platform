#!/usr/bin/env bash
# Build all Docker images for MCP Docking System
# Linux/macOS version of docker_build.ps1

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo "================================================================================"
echo -e "${CYAN}MCP Docking System - Docker Image Builder${NC}"
echo "================================================================================"
echo ""

# Parse arguments
NO_CACHE=""
SERVICES=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        --service)
            SERVICES+=("$2")
            shift 2
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Default to building all services
if [ ${#SERVICES[@]} -eq 0 ]; then
    SERVICES=("base" "rcsb-downloader" "ligand-preparer" "protein-preparer" "moldina-runner" "pymol-visualizer" "pymol-interactive" "ui")
fi

echo -e "${GREEN}Building services: ${SERVICES[*]}${NC}"
echo ""

# Build function
build_image() {
    local service=$1
    local dockerfile=$2
    local tag=$3
    local description=$4
    
    echo "────────────────────────────────────────────────────────────────────────────"
    echo -e "${CYAN}📦 Building: $service${NC}"
    echo -e "   Description: $description"
    echo -e "   Dockerfile: $dockerfile"
    echo -e "   Tag: $tag"
    echo ""
    
    start_time=$(date +%s)
    
    if docker build -f "$dockerfile" -t "$tag" $NO_CACHE .; then
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        echo -e "${GREEN}✅ Successfully built $service in ${duration}s${NC}"
        echo ""
        return 0
    else
        echo -e "${RED}❌ Failed to build $service${NC}"
        echo ""
        return 1
    fi
}

# Build images
success_count=0
fail_count=0
total_start_time=$(date +%s)

for service in "${SERVICES[@]}"; do
    case $service in
        base)
            build_image "base" "docker/base.Dockerfile" "mcp-docking-base:latest" "Base image with Python and scientific libraries"
            ;;
        rcsb-downloader)
            build_image "rcsb-downloader" "docker/rcsb_downloader.Dockerfile" "mcp-rcsb-downloader:latest" "RCSB PDB structure downloader"
            ;;
        ligand-preparer)
            build_image "ligand-preparer" "docker/ligand_preparer.Dockerfile" "mcp-ligand-preparer:latest" "Ligand preparation service"
            ;;
        protein-preparer)
            build_image "protein-preparer" "docker/protein_preparer.Dockerfile" "mcp-protein-preparer:latest" "Protein preparation service"
            ;;
        moldina-runner)
            build_image "moldina-runner" "docker/moldina_runner.Dockerfile" "mcp-moldina-runner:latest" "Moldina molecular docking service"
            ;;
        pymol-visualizer)
            build_image "pymol-visualizer" "docker/pymol_visualizer.Dockerfile" "mcp-pymol-visualizer:latest" "PyMOL visualization service"
            ;;
        pymol-interactive)
            build_image "pymol-interactive" "docker/pymol_interactive.Dockerfile" "mcp-pymol-interactive:latest" "Interactive PyMOL service (noVNC + helper API)"
            ;;
        ui)
            build_image "ui" "Dockerfile.ui" "mcp-docking-ui:latest" "NiceGUI web UI"
            ;;
        *)
            echo -e "${RED}Unknown service: $service${NC}"
            ((fail_count++))
            continue
            ;;
    esac
    
    if [ $? -eq 0 ]; then
        ((success_count++))
    else
        ((fail_count++))
        echo -e "${YELLOW}⚠️  Stopping build due to failure${NC}"
        break
    fi
done

total_end_time=$(date +%s)
total_duration=$((total_end_time - total_start_time))

echo "================================================================================"
echo -e "${CYAN}BUILD SUMMARY${NC}"
echo "================================================================================"
echo ""
echo -e "${GREEN}✅ Successful: $success_count${NC}"
echo -e "${RED}❌ Failed: $fail_count${NC}"
echo -e "${CYAN}⏱️  Total time: ${total_duration}s${NC}"
echo ""

if [ $fail_count -eq 0 ]; then
    echo -e "${GREEN}🎉 All images built successfully!${NC}"
    echo ""
    echo -e "${CYAN}Next steps:${NC}"
    echo "  1. View images: docker images | grep mcp"
    echo "  2. Start services: docker-compose up -d"
    echo "  3. Check status: docker-compose ps"
    echo "  4. View logs: docker-compose logs -f"
    exit 0
else
    echo -e "${RED}❌ Build failed. Check errors above.${NC}"
    exit 1
fi
