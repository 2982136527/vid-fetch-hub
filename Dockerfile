FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg (for HLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgtk-3-0 libgdk-pixbuf2.0-0 libnotify4 libsoup-3.0-0 \
    libwebkit2gtk-4.1-0 libxdamage1 \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium (ACFAN cover decryption)
RUN python3 -m playwright install chromium && \
    python3 -m playwright install-deps chromium 2>&1

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
