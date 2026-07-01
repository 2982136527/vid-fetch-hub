FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg (for HLS) + Chromium deps (for Playwright/ACFAN covers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2 libpango-1.0-0 libcairo2 libcups2 libdbus-1-3 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser
RUN python3 -m playwright install chromium

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
