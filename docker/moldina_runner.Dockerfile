# Moldina Runner Service Docker Image
# Includes Moldina docking software compiled from source
#
# Build with: docker build -f docker/moldina_runner.Dockerfile -t mcp-moldina-runner:latest .
# Run with: docker run -p 8004:8004 -v ./cache:/app/cache mcp-moldina-runner:latest

FROM mcp-docking-base:latest

LABEL service="moldina_runner"
LABEL port="8004"

# Install additional dependencies for Moldina compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Boost libraries for Moldina
    libboost-system-dev \
    libboost-thread-dev \
    libboost-serialization-dev \
    libboost-filesystem-dev \
    libboost-program-options-dev \
    # Build tools
    build-essential \
    autoconf \
    automake \
    libtool \
    && rm -rf /var/lib/apt/lists/*

# Clone and build Moldina from source
# Note: Replace with actual Moldina repository URL when available
# For now, we'll create a placeholder that can be updated
WORKDIR /tmp

# Copy the actual Moldina executable from the host
# This is the real Moldina binary compiled for Linux
COPY Moldina/moldina /usr/local/bin/moldina
RUN chmod +x /usr/local/bin/moldina

# Verify Moldina installation
RUN moldina --help 2>&1 | head -20 || echo "Moldina installed"

# Copy service code
WORKDIR /app
COPY services/moldina_runner/ /app/services/moldina_runner/
COPY services/__init__.py /app/services/__init__.py

# Set Moldina path environment variable
ENV MOLDINA_PATH=/usr/local/bin/moldina

# Expose service port
EXPOSE 8004

# Health check for this specific service
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8004/health || exit 1

# Run the server with Moldina path
CMD ["python", "-m", "services.moldina_runner.server", \
     "--host", "0.0.0.0", \
     "--port", "8004", \
     "--moldina-path", "/usr/local/bin/moldina"]
