FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg (for HLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium (for ACFAN cover decryption)
# Use install-deps to pull missing system libraries
RUN python3 -m playwright install chromium && \
    python3 -m playwright install-deps chromium 2>&1 || echo "Playwright deps may be incomplete, continuing..."

COPY . .

# Move config to /config so DSM Docker UI shows it clearly
RUN mkdir -p /config && \
    cp /app/config.yaml /config/config.yaml

# Create output directories
RUN mkdir -p /output
VOLUME ["/output", "/config"]

EXPOSE 8383

# Default: auto daemon mode
CMD ["python3", "main.py", "--auto"]
