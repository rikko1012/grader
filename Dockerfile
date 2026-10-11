# CS2 Scout grader - the Python server as it ran on Railway, for any Docker host (Render, Fly.io, a VPS).
# Persistent data (pick log, caches, FACEIT ids) lives in CS2_DATA_DIR - mount a disk there.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CS2_DATA_DIR=/data \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data

EXPOSE 8000
CMD ["python", "server.py"]
