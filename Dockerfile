# ── Stage 1: Build ──
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv pip install --system --no-cache ".[dev]"

COPY src/ src/
RUN uv pip install --system --no-cache --no-deps -e .

# ── Stage 2: Runtime ──
FROM python:3.13-slim AS runtime

# External tools required by subprocess-based PDF tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    qpdf \
    ghostscript \
    ocrmypdf \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    libreoffice-writer-nogui \
    && rm -rf /var/lib/apt/lists/*

# Copy Python environment from builder
COPY --from=builder /usr/local/lib/python3.13 /usr/local/lib/python3.13
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app
COPY --from=builder /app /app
COPY alembic/ alembic/
COPY alembic.ini .

# Create non-root user
RUN groupadd -r pdfagent && useradd -r -g pdfagent -d /app pdfagent && \
    mkdir -p /app/data && chown -R pdfagent:pdfagent /app/data /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

USER pdfagent
EXPOSE 8000

CMD ["uvicorn", "pdf_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
