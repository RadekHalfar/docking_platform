# Protein Preparer Service Docker Image
# Requires chemistry libraries for protein processing
#
# Build with: docker build -f docker/protein_preparer.Dockerfile -t mcp-protein-preparer:latest .
# Run with: docker run -p 8003:8003 -v ./cache:/app/cache mcp-protein-preparer:latest

FROM mcp-docking-base:latest

LABEL service="protein_preparer"
LABEL port="8003"

# Copy service code
COPY services/protein_preparer/ /app/services/protein_preparer/
COPY services/__init__.py /app/services/__init__.py

# Set working directory
WORKDIR /app

# Expose service port
EXPOSE 8003

# Health check for this specific service
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

# No further root-level setup needed after this point -- run as the
# non-root user created in base.Dockerfile.
USER appuser

# Run the server
CMD ["python", "-m", "services.protein_preparer.server", "--host", "0.0.0.0", "--port", "8003"]
