FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Only install essential system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first (caching magic!)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy the rest of your code
COPY . .

CMD ["python", "bot.py"]
