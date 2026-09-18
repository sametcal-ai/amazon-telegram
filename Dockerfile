FROM python:3.12-slim

# Python loglarının anlık konsola düşmesi için
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Gerekli sistem paketlerini kur
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Bağımlılıkları kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını kopyala
COPY . .

# Kalıcı veritabanı klasörünü oluştur
RUN mkdir -p /app/data

CMD ["python", "-u", "main.py"]
