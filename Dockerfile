FROM python:3.11-slim

# Install system dependencies and fonts for headless browser rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    fontconfig \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-cjk \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package metadata and source code
COPY pyproject.toml LICENSE README.md ./
COPY src/ ./src/

# Install TabPilot package
RUN pip install --no-cache-dir .

# Create and switch to non-root user
RUN useradd -m -u 1000 tabpilot && \
    mkdir -p /home/tabpilot/.tabpilot && \
    chown -R tabpilot:tabpilot /home/tabpilot /app
USER tabpilot

# Expose stdio MCP server entrypoint
ENTRYPOINT ["tabpilot", "serve"]
