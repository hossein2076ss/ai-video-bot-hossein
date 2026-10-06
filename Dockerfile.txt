FROM python:3.10-slim

# نصب FFmpeg و ابزارهای گرافیکی مورد نیاز برای پردازش ویدیو
RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    && rm -rf /var/lib/apt-get/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]