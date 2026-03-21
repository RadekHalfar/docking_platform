# RCSB Downloader Service Docker Image
# Simple service with no external tool dependencies
#
# Build with: docker build -f docker/rcsb_downloader.Dockerfile -t mcp-rcsb-downloader:latest .
# Run with: docker run -p 8001:8001 -v ./cache:/app/cache mcp-rcsb-downloader:latest

FROM mcp-docking-base:latest

LABEL service="rcsb_downloader"
LABEL port="8001"

# Copy service code
COPY services/rcsb_downloader/ /app/services/rcsb_downloader/
COPY services/__init__.py /app/services/__init__.py

# Set working directory
WORKDIR /app

# Expose service port
EXPOSE 8001

# Health check for this specific service
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Run the server
CMD ["python", "-m", "services.rcsb_downloader.server", "--host", "0.0.0.0", "--port", "8001"]
