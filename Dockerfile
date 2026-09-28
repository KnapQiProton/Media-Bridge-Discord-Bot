FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies untuk Telethon / cryptg + curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install cloudflared binary globally (sebagai cadangan auto-tunnel)
RUN curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Siapkan user televid dengan izin direktori yang tepat
RUN useradd -m -u 1000 televid && \
    mkdir -p /home/televid/.cloudflared /app && \
    chown -R televid:televid /home/televid /app

USER televid

COPY --chown=televid:televid . .

EXPOSE 8080

CMD ["python", "bot.py"]
