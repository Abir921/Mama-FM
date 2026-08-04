# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so code changes don't invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py db.py sync_commands.py set_avatar.py ./
COPY cogs/ ./cogs/

# mama.db lives here, mounted as a volume so links and polls survive restarts.
VOLUME ["/data"]
ENV MAMA_DB_PATH=/data/mama.db

CMD ["python", "bot.py"]
