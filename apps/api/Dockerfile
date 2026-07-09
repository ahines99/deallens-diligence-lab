FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[postgres]"

# Runtime data dir (SQLite fallback / local cache).
RUN mkdir -p /app/data

EXPOSE 8000

# AUTO_SEED (default true) loads the ChainAssure workspace on first boot.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
