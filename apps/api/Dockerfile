FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml requirements.lock README.md ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --require-hashes -r requirements.lock \
    && pip install --no-build-isolation --no-deps .

# Runtime user and data dir (SQLite fallback / local cache).
RUN useradd --system --uid 10001 --create-home deallens \
    && mkdir -p /app/data \
    && chown -R deallens:deallens /app/data

USER deallens

EXPOSE 8000

# Migrations run before the non-root API process starts; AUTO_SEED is disabled by default.
CMD ["sh", "-c", "python -m alembic -c alembic.ini upgrade head && exec uvicorn src.main:app --host 0.0.0.0 --port 8000"]
