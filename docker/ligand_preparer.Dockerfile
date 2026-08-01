# Ligand Preparer Service Docker Image
# Requires RDKit and Meeko for ligand processing
#
# Build with: docker build -f docker/ligand_preparer.Dockerfile -t mcp-ligand-preparer:latest .
# Run with: docker run -p 8002:8002 -v ./cache:/app/cache mcp-ligand-preparer:latest

FROM mcp-docking-base:latest

LABEL service="ligand_preparer"
LABEL port="8002"

# Copy service code
COPY services/ligand_preparer/ /app/services/ligand_preparer/
COPY services/__init__.py /app/services/__init__.py

# Set working directory
WORKDIR /app

# Expose service port
EXPOSE 8002

# Health check for this specific service
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

# No further root-level setup needed after this point -- run as the
# non-root user created in base.Dockerfile.
USER appuser

# Run the server
CMD ["python", "-m", "services.ligand_preparer.server", "--host", "0.0.0.0", "--port", "8002"]
