# Use official Python image
FROM python:3.11-slim

# Prevent Python from writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# -------------------------------------------------------
# Install Playwright & Chromium dependencies
# -------------------------------------------------------
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    ca-certificates \
    fonts-liberation \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libxcb1 \
    libxshmfence1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------
# Install Python dependencies
# -------------------------------------------------------
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip

RUN pip install --no-cache-dir -r requirements.txt

# -------------------------------------------------------
# Install Playwright Chromium
# -------------------------------------------------------
RUN python -m playwright install chromium

# -------------------------------------------------------
# Copy source code
# -------------------------------------------------------
COPY . .

# -------------------------------------------------------
# Expose FastAPI port
# -------------------------------------------------------
EXPOSE 8040

# -------------------------------------------------------
# Start FastAPI
# -------------------------------------------------------
CMD ["python", "main.py"]
