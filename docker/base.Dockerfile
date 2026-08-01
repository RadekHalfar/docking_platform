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
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r /app/requirements.txt

# Copy common modules
COPY common/ /app/common/

# Non-root user for services that don't need extra root-level setup after
# this point. Not switched to by default here -- some derived images
# (pymol_visualizer, pymol_interactive) need their own root-level apt-get/
# conda installs and stay root for the whole build; services that don't
# need that opt in with `USER appuser` near the end of their own Dockerfile.
# Root still bypasses these permission bits entirely, so tightening 777 to
# 755 (owned by appuser) doesn't affect images that stay root -- it only
# stops it being world-writable for images that switch to appuser.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser && \
    mkdir -p /app/cache /app/data /app/output && \
    chown -R appuser:appuser /app/cache /app/data /app/output && \
    chmod -R 755 /app/cache /app/data /app/output

# Health check disabled in base image; each service image or compose stanza provides its own.
HEALTHCHECK NONE

# Default command (override in service-specific images)
CMD ["python", "-m", "flask", "run"]
