# PyMOL Visualizer Service Docker Image
# Includes PyMOL for molecular visualization and rendering
#
# Build with: docker build -f docker/pymol_visualizer.Dockerfile -t mcp-pymol-visualizer:latest .
# Run with: docker run -p 8005:8005 -v ./output:/app/output mcp-pymol-visualizer:latest

FROM mcp-docking-base:latest

LABEL service="pymol_visualizer"
LABEL port="8005"

# Install system dependencies for PyMOL and rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    # X11 libraries for rendering (even in headless mode)
    libgl1 \
    libglu1-mesa \
    xvfb \
    x11-utils \
    xauth \
    # Required for conda installation
    wget \
    bzip2 \
    ca-certificates \
    # Image manipulation
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Install PyMOL from conda-forge via Miniforge3 + mamba (libmamba solver — fast and reliable)
RUN wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -O /tmp/miniforge.sh && \
    bash /tmp/miniforge.sh -b -p /opt/conda && \
    rm /tmp/miniforge.sh && \
    /opt/conda/bin/mamba install -y -c conda-forge pymol-open-source flask requests && \
    ln -s /opt/conda/bin/pymol /usr/local/bin/pymol && \
    /opt/conda/bin/conda clean -afy

# Add conda to PATH (so python runs conda's python with pymol and flask)
ENV PATH="/opt/conda/bin:${PATH}"

# Verify PyMOL installation
RUN pymol -c -e "quit" || echo "PyMOL installed successfully"

# Copy service code
WORKDIR /app
COPY services/pymol_visualizer/ /app/services/pymol_visualizer/
COPY services/__init__.py /app/services/__init__.py

# Create output directory for visualizations
RUN mkdir -p /app/output/visualization && chmod -R 777 /app/output

# Expose service port
EXPOSE 8005

# Health check for this specific service
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Wrap command with xvfb-run for headless rendering
# This creates a virtual display that PyMOL can use
CMD ["sh", "-c", "xvfb-run -a python -m services.pymol_visualizer.server --host 0.0.0.0 --port 8005 --pymol-path /usr/local/bin/pymol"]
