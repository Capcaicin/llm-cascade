# Single image, two entrypoints — docker-compose picks which command to run.
FROM python:3.12-slim

WORKDIR /app

# System deps — none required beyond python for pure-http stack. Add curl for
# container-level healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps. We vendor minimal requirements here so the image rebuilds fast
# when app code changes but deps don't.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure `core/` is importable when running scripts from /app/src
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 3839 8501

# Default — docker-compose overrides per service
CMD ["python", "src/router_server.py"]
