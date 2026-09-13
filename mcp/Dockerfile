FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

# Copy application code and configuration
COPY pyproject.toml /app/
COPY main.py /app/
COPY src/ /app/src/

# Install dependencies and sync project in virtualenv
RUN uv sync --no-dev

# Ensure python can find 'src'
ENV PYTHONPATH="/app:/app/src"

CMD ["uv", "run", "main.py"]
