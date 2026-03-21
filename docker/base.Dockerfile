# Base Docker image for MCP Docking System services
# Contains Python and common scientific libraries used by multiple services
#
# Build with: docker build -f docker/base.Dockerfile -t mcp-docking-base:latest .

FROM python:3.12-slim

LABEL maintainer="MCP Docking System"
LABEL description="Base image with Python and scientific computing libraries"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies required by scientific libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    gcc \
    g++ \
    make \
    cmake \
    # Required for RDKit and chemistry libraries
    libboost-all-dev \
    libcairo2-dev \
    libeigen3-dev \
    # Required for OpenBabel
    libxml2-dev \
    zlib1g-dev \
    # Utilities
    curl \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create application directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt /app/

# Install Python dependencies
# Split into layers for better caching
RUN pip install --upgrade pip setuptools wheel

# Install core dependencies (changes rarely)
RUN pip install \
    requests>=2.31.0 \
    flask>=3.0.0 \
    python-dotenv>=1.0.0 \
    pytest>=7.4.0 \
    pytest-cov>=4.1.0 \
    pytest-mock>=3.12.0 \
    pydantic>=2.5.0 \
    jsonschema>=4.20.0 \
    pyyaml>=6.0.1

# Install chemistry packages (heavy, cache separately)
RUN pip install \
    numpy>=1.24.0 \
    scipy>=1.11.0 \
    rdkit>=2023.9.1 \
    meeko>=0.5.0 \
    gemmi>=0.6.0 \
    openbabel-wheel>=3.1.1

# Copy common modules
COPY common/ /app/common/

# Create cache and data directories
RUN mkdir -p /app/cache /app/data /app/output && \
    chmod -R 777 /app/cache /app/data /app/output

# Health check (override in service-specific images)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (override in service-specific images)
CMD ["python", "-m", "flask", "run"]
