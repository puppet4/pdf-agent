FROM python:3.13-slim

# Install system dependencies for PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    qpdf \
    poppler-utils \
    ghostscript \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    ocrmypdf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application
COPY src/ src/
COPY alembic.ini .
COPY alembic/ alembic/

# Create data directories
RUN mkdir -p data/uploads data/jobs

EXPOSE 8000

CMD ["uvicorn", "pdf_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
