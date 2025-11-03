# Dockerfile for FastAPI Backend
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
# Note: Railway uses root as build context, so we need backend/ prefix
COPY backend/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Node.js for frontend build (before copying backend to avoid cache invalidation)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    node --version && npm --version

# Copy frontend files for build
COPY package*.json ./
COPY vite.config.js ./
COPY src/ ./src/
COPY index.html ./

# Copy public directory if it exists (optional for Vite)
# Use a shell workaround to conditionally copy
RUN mkdir -p /tmp/public && \
    if [ -d "public" ]; then cp -r public/* /tmp/public/ 2>/dev/null || true; fi

# Build frontend
RUN if [ -f "package.json" ]; then \
        echo "Building frontend..." && \
        npm install && \
        export VITE_API_BASE_URL=/api && \
        npm run build && \
        echo "Frontend build completed" && \
        ls -la dist/ && \
        mkdir -p backend/static && \
        cp -r dist/* backend/static/ && \
        echo "Frontend copied to backend/static" && \
        ls -la backend/static/ && \
        rm -rf node_modules src index.html vite.config.js package*.json dist; \
    else \
        echo "No package.json found, skipping frontend build"; \
    fi

# Copy application code
COPY backend/ .

# Create logs directory
RUN mkdir -p logs

# Expose port
EXPOSE 3000

# Health check (using curl if available, otherwise simple Python check)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3000/api/health')" || exit 1

# Run the application
CMD ["python", "main.py"]

