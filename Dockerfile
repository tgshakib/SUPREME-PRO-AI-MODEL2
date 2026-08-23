FROM python:3.11-slim

WORKDIR /app

# Avoid .pyc clutter, force unbuffered output for live logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System libs: needed by pandas / yfinance HTTPS, plus tzdata for timezones
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tzdata \ git\y
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite DB lives next to the source — mount a volume here for persistence


CMD ["python", "bot.py"]
