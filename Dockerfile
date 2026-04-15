# Multi-stage build for optimal image size and build times
FROM python:3.11-slim as builder

LABEL stage=builder

# Install system dependencies required for database drivers
# - build-essential, gcc, g++: Required for compiling Python packages
# - unixodbc-dev: Required for pyodbc (SQL Server driver)
# - libpq-dev: Required for psycopg2 (PostgreSQL driver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    unixodbc-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment in builder stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements file
COPY requirements.txt .

# Install all Python dependencies in virtual environment
RUN pip install --no-cache-dir -r requirements.txt

# ============================================================================
# Final stage - minimal production runtime image
# ============================================================================
FROM python:3.11-slim

LABEL maintainer="Karpagavalli - Optisol Business Solutions"
LABEL description="MCP Server for database schema metadata extraction"
LABEL version="0.1.2"

# Install only runtime dependencies (no build tools)
# - unixodbc: Runtime library for pyodbc
# - libpq5: Runtime library for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder (all dependencies pre-compiled)
COPY --from=builder /opt/venv /opt/venv

# Set up environment variables
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Create non-root user for security
RUN useradd -m -u 1000 mcpuser

# Copy application code
COPY --chown=mcpuser:mcpuser src/ /app/src/

# Set working directory
WORKDIR /app

# Switch to non-root user
USER mcpuser

# Health check (optional, verifies server can start)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -m db_metadata_extractor.server --help || exit 1

# Expose port for documentation (server runs on stdio, not HTTP)
EXPOSE 8000

# Entry point: Start the MCP stdio server
ENTRYPOINT ["python", "-m", "db_metadata_extractor.server"]
CMD []
