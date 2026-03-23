# PyMOL Interactive Service Docker Image
# Runs desktop PyMOL in Docker with noVNC browser access + helper API.

FROM mcp-docking-base:latest

LABEL service="pymol_interactive"
LABEL novnc_port="6080"
LABEL api_port="8090"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    xvfb \
    x11-utils \
    xauth \
    x11vnc \
    novnc \
    websockify \
    openbox \
    wget \
    bzip2 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install PyMOL and helper runtime using conda-forge.
RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p /opt/conda && \
    rm /tmp/miniconda.sh && \
    /opt/conda/bin/conda config --add channels conda-forge && \
    /opt/conda/bin/conda config --set channel_priority strict && \
    /opt/conda/bin/conda install -y --override-channels -c conda-forge pymol-open-source flask requests && \
    ln -sf /opt/conda/bin/pymol /usr/local/bin/pymol && \
    /opt/conda/bin/conda clean -afy

ENV PATH="/opt/conda/bin:${PATH}"
ENV DISPLAY=":99"
ENV PYMOL_RPC_URL="http://127.0.0.1:9123"
ENV PYMOL_INTERACTIVE_PORT="8090"

WORKDIR /app
COPY services/pymol_interactive/ /app/services/pymol_interactive/
COPY services/__init__.py /app/services/__init__.py

RUN mkdir -p /app/orchestrator_output /app/demo_output /tmp/runtime && chmod -R 777 /tmp/runtime /app/orchestrator_output /app/demo_output

COPY docker/pymol_interactive_start.sh /usr/local/bin/pymol_interactive_start.sh
RUN chmod +x /usr/local/bin/pymol_interactive_start.sh

EXPOSE 6080 8090

HEALTHCHECK --interval=30s --timeout=10s --start-period=50s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

CMD ["/usr/local/bin/pymol_interactive_start.sh"]
