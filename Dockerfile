FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements. constraints.txt is referenced from requirements.txt, and
# Oryx/pip only see a file that is present before install.
COPY requirements.txt constraints.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz')" || exit 1

# Same worker class, thread count, and timeouts as the Procfile. PORT defaults
# to 5000 here so the image health check matches; Azure sets PORT itself.
ENV PORT=5000
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
