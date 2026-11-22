# Python 3.12 slim image (matches the CI and the version the suite is verified on)
FROM python:3.12-slim-bookworm

# Set working directory
WORKDIR /app

# Install system dependencies including Playwright browser deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY backend/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and Chromium browser (shared path so the non-root user can use it)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright && playwright install chromium

# Run as an unprivileged user, never root
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app

# Copy application code
COPY --chown=app:app backend/ .

# Create uploads directory (writable by the app user)
RUN mkdir -p uploads && chown app:app uploads

USER app

# Expose port (Railway uses dynamic $PORT)
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)"

# Run migrations and start the application
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
