# Gunakan image Python 3.11 slim yang ringan
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependensi sistem yang dibutuhkan untuk kompilasi ringan (jika diperlukan asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Buat user non-root untuk keamanan
RUN useradd -m -u 1000 televid
USER televid

# Salin kode aplikasi
COPY --chown=televid:televid . .

# Jalankan bot
CMD ["python", "bot.py"]
